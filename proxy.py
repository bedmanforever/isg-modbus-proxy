#!/usr/bin/env python3
"""Read-only ISG v12 web scraper with limited Modbus holding-register writes."""
import json, logging, os, re, threading, time
from html.parser import HTMLParser
from urllib.parse import urljoin
import requests
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.server import StartTcpServer

LOG = logging.getLogger("isg-proxy")
ISG_URL = os.getenv("ISG_URL", "http://isg").rstrip("/")
POLL_INTERVAL = max(2, int(os.getenv("POLL_INTERVAL", "30")))
PORT = int(os.getenv("MODBUS_PORT", "502"))
USER = os.getenv("ISG_USERNAME")
PASSWORD = os.getenv("ISG_PASSWORD")
INVALID = 0xFFFF

# Address -> (field key, multiplier, kind); values in canonical ISG units.
REG_FIELDS = {
  500: ("outdoor", 10, "temp"), 501: ("flow_wp", 10, "temp"),
  502: ("return", 10, "temp"), 503: ("dhw_actual", 10, "temp"),
  504: ("hk1_actual", 10, "temp"), 505: ("hk2_actual", 10, "temp"),
  506: ("flow_nhz", 10, "temp"), 507: ("condenser", 10, "temp"),
  508: ("hk1_set", 10, "temp"), 509: ("hk2_set", 10, "temp"),
  800: ("dhw_comfort", 10, "temp_write"),
  801: ("dhw_eco", 10, "temp_write"), 810: ("dhw_upper_set", 10, "temp"),
  1500: ("mode", 1, "mode_write"),
  1520: ("compressor_heat_hours", 1, "integer"),
  1521: ("compressor_dhw_hours", 1, "integer"),
  1522: ("nhz1_hours", 1, "integer"), 1523: ("nhz2_hours", 1, "integer"),
  1524: ("compressor_heat_day_kwh", 1, "integer"),
  1525: ("compressor_heat_total_mwh", 1, "integer"),
  1526: ("compressor_dhw_day_kwh", 1, "integer"),
  1527: ("compressor_dhw_total_mwh", 1, "integer"),
  1528: ("nhz_heat_total_mwh", 1, "integer"),
  1530: ("heat_pump_power_pct", 1, "integer"),
  1531: ("pump_power_pct", 1, "integer"),
}
MODE_NAMES = {0:"Bereitschaftsbetrieb",1:"Programmbetrieb",2:"Komfortbetrieb",3:"Eco-Betrieb",4:"Warmwasserbetrieb",5:"Notbetrieb"}

class Page(HTMLParser):
    """Minimal structure capture: table rows and each calibration label/JS value."""
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rows=[];self.row=[];self.cell=None
        self.cal=[]; self.in_cal=False;self.cal_depth=0;self.label=False;self.label_text='';self.scripts=[];self.script=False;self.inputs=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); cls=a.get('class','')
        if tag=='tr': self.row=[]
        if tag in ('td','th'): self.cell=[]
        if tag=='div' and 'calibration' in cls.split(): self.in_cal=True;self.cal_depth=1;self.label_text='';self.scripts=[];self.inputs=[]
        elif self.in_cal and tag=='div': self.cal_depth+=1
        if self.in_cal and tag=='h3' and 'title' in cls.split(): self.label=True
        if self.in_cal and tag=='script': self.script=True; self.scripts.append('')
        if tag=='input': self.inputs.append(a)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split())); self.cell=None
        if tag=='tr' and self.row: self.rows.append(self.row)
        if self.in_cal and tag=='h3': self.label=False
        if self.in_cal and tag=='script': self.script=False
        if self.in_cal and tag=='div':
            self.cal_depth-=1
            if self.cal_depth==0:
                self.cal.append((self.label_text.strip(),''.join(self.scripts),self.inputs));self.in_cal=False
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
        if self.in_cal and self.label:self.label_text+=data
        if self.in_cal and self.script and self.scripts:self.scripts[-1]+=data

def number(text):
    s=(text or '').replace('\xa0',' ').strip(); s=re.sub(r'[^0-9,.-]','',s)
    if not s:return None
    # Values in these ISG pages use decimal comma; dots in long totals are thousands separators.
    if ',' in s:s=s.replace('.','').replace(',','.')
    try:return float(s)
    except ValueError:return None

def parse_page(markup):
    p=Page();p.feed(markup); out={}
    # Status/diagnosis pages render actual readings as table rows; map by exact labels.
    labels={
      'AUSSENTEMPERATUR':'outdoor','VORLAUFISTTEMPERATUR WP':'flow_wp','RÜCKLAUFISTTEMPERATUR':'return',
      'ISTTEMPERATUR':'dhw_actual','ISTTEMPERATUR HK 1':'hk1_actual','ISTTEMPERATUR HK 2':'hk2_actual',
      'VORLAUFISTTEMPERATUR NHZ':'flow_nhz','VERFLÜSSIGERTEMP.':'condenser',
      'SOLLTEMPERATUR HK 1':'hk1_set','SOLLTEMPERATUR HK 2':'hk2_set',
      'VD HEIZEN':'compressor_heat_hours','VD WARMWASSER':'compressor_dhw_hours',
      'NHZ 1':'nhz1_hours','NHZ 2':'nhz2_hours','VD HEIZEN TAG':'compressor_heat_day_kwh',
      'VD HEIZEN SUMME':'compressor_heat_total_mwh','VD WARMWASSER TAG':'compressor_dhw_day_kwh',
      'VD WARMWASSER SUMME':'compressor_dhw_total_mwh','NHZ HEIZEN SUMME':'nhz_heat_total_mwh',
      'LEISTUNG WP':'heat_pump_power_pct','LSTG PU PUMPE':'pump_power_pct'}
    for row in p.rows:
        if len(row)>=2:
            label=re.sub(r'\s+',' ',row[0]).strip().upper()
            if label in labels:
                val=number(row[1])
                if val is not None:out[labels[label]]=val
    # Editable setting values: jsvalues[id].val is the displayed value; identify by title/key.
    for label,script,inputs in p.cal:
        found=re.search(r"jsvalues\['(\d+)'\].*?\['val'\]\s*=\s*'([^']*)'",script,re.S)
        if not found:continue
        key,val=found.groups(); v=number(val)
        l=label.upper()
        if v is None:continue
        if key=='60312' and 'OBERE WARMWASSER SOLLTEMP.' in l:out['dhw_upper_set']=v
        elif key=='22' and 'SOLLTEMP. KOMFORT' in l:out['dhw_comfort']=v
        elif key=='23' and 'SOLLTEMPERATUR ECO' in l:out['dhw_eco']=v
        elif key=='16' and 'RAUMSOLLTEMP. KOMFORT' in l:out['hk1_set']=v
        elif key=='17' and 'RAUMSOLLTEMP. ECO' in l:out['hk2_set']=v
    # ISG dashboard mode has a hidden field val1, not jsvalues.
    mode=re.search(r'<input[^>]*\bname=["\']val1["\'][^>]*\bvalue=["\'](\d+)',markup,re.I)
    if not mode:mode=re.search(r'<input[^>]*\bvalue=["\'](\d+)["\'][^>]*\bname=["\']val1',markup,re.I)
    if mode:out['mode']=int(mode.group(1))
    return out

def encode_register(key,value):
    if value is None:return INVALID
    scale=next((mult for field,mult,_ in REG_FIELDS.values() if field==key),1)
    raw=int(round(float(value)*scale))
    return raw & 0xFFFF

class Store(ModbusSequentialDataBlock):
    def __init__(self):
        super().__init__(0,[INVALID]*2001);self.lock=threading.RLock();self.values_by_key={};self.session=requests.Session()
        if USER:self.session.auth=(USER,PASSWORD or '')
        self.base=ISG_URL
    def setValues(self,address,values):
        if not isinstance(values,list):values=[values]
        # pymodbus 3.6.9 ignores setValues returns; raised failures become exception 0x04.
        with self.lock:
            if len(values)!=1:
                raise ValueError('Write one register per request; ISG writes are not atomic')
            self.handle_write(address,values[0])
    def getValues(self,address,count=1):
        with self.lock:
            return super().getValues(address,count)
    def update(self,data):
        with self.lock:
            self.values_by_key.update(data)
            for reg,(key,mult,kind) in REG_FIELDS.items():
                if key in data:
                    self.values[reg]=encode_register(key,data[key])
    def handle_write(self,reg,raw):
        if reg not in (800,801,1500):raise ValueError('Register is read-only or unsupported')
        if not isinstance(raw,int) or not 0<=raw<=0xffff:raise ValueError('Expected a 16-bit register value')
        key,scale,_=REG_FIELDS[reg]
        value=raw-65536 if raw&0x8000 else raw
        value=value/scale
        if reg in (800,801):
            if not 10<=value<=60:raise ValueError('DHW setpoint allowed range is 10..60 °C')
            payload=[{'name':'val22' if reg==800 else 'val23','value':str(value).replace('.',',')}]
        else:
            if value not in MODE_NAMES:raise ValueError('mode must be 0..5')
            payload=[{'name':'val1','value':str(int(value))},{'name':'val1s','value':str(int(value))}]
        # Inferred from scripts.js, not a captured write; verify on the target firmware.
        resp=self.session.post(urljoin(self.base+'/', 'save.php'),data={'data':json.dumps(payload,separators=(',',':'))},timeout=10)
        resp.raise_for_status()
        try:
            result=resp.json()
        except requests.exceptions.JSONDecodeError:
            raise RuntimeError('ISG write response is not JSON')
        if not isinstance(result,dict) or result.get('success') is not True:
            raise RuntimeError('ISG did not confirm write success')
        self.values_by_key[key]=value;self.values[reg]=raw
        LOG.info('Posted ISG write for register %s (%s=%s)',reg,key,value)

STORE=Store()
def poll_once(session=None,base=None):
    s=session or STORE.session;base=(base or ISG_URL).rstrip('/'); merged={}
    for page in ('?s=1,0','?s=1,1','?s=4,1','?s=4,2',''):
        try:
            r=s.get(base+'/'+page if page else base+'/',timeout=10);r.raise_for_status(); merged.update(parse_page(r.text))
        except Exception as e:LOG.warning('Poll parse/fetch failed for %s: %s',page or '/',e)
    return merged

def poller():
    while True:
        try:STORE.update(poll_once())
        except Exception:LOG.exception('Unexpected poll failure')
        time.sleep(POLL_INTERVAL)

def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(message)s')
    threading.Thread(target=poller,daemon=True,name='isg-poller').start()
    context=ModbusServerContext(slaves=ModbusSlaveContext(hr=STORE,zero_mode=True),single=True)
    LOG.info('Serving Modbus TCP on 0.0.0.0:%d; ISG=%s poll=%ss',PORT,ISG_URL,POLL_INTERVAL)
    StartTcpServer(context=context, address=("0.0.0.0", PORT))
if __name__=='__main__':main()
