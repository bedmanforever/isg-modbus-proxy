import os,sys,threading,time
sys.path.insert(0,os.path.dirname(__file__))
import proxy
from pymodbus.client import ModbusTcpClient
from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext,ModbusServerContext

class Resp:
    def __init__(self,text):self.text=text;self.status_code=200
    def raise_for_status(self):pass
class FakeSession:
    def __init__(self):self.calls=[]
    def get(self,url,timeout=10):
        self.calls.append(url)
        # Local fixture page content, no network involved.
        if '?s=1,0' in url:return Resp(open('/mnt/user-data/uploads/isg-1_0.html',encoding='utf8').read())
        return Resp('<html></html>')
    def post(self,url,data,timeout=10):
        self.calls.append((url,data));r=Resp('{"success":true}');r.json=lambda:{'success':True};return r

# Mock poll of one actual uploaded page; routes to empty fixture for all others.
s=FakeSession(); data=proxy.poll_once(session=s,base='http://local-fixture')
assert data['outdoor']==9.2 and data['flow_wp']==24.2 and data['return']==23.8 and data['dhw_actual']==46.0
proxy.STORE.update(data)
assert proxy.STORE.getValues(500,1)==[92]
assert proxy.STORE.getValues(501,1)==[242]
assert proxy.STORE.getValues(503,1)==[460]
assert proxy.STORE.getValues(1000,1)==[proxy.INVALID]
proxy.STORE.session=s
# No need to contact ISG; requests posts are captured by fake session.
ctx=ModbusServerContext(slaves=ModbusSlaveContext(hr=proxy.STORE,zero_mode=True),single=True)
port=15029
threading.Thread(target=StartTcpServer,kwargs={'context':ctx,'address':('127.0.0.1',port)},daemon=True).start()
time.sleep(.5)
c=ModbusTcpClient('127.0.0.1',port=port);assert c.connect()
r=c.read_holding_registers(500,3,slave=1);assert not r.isError() and r.registers==[92,242,238],r
w=c.write_register(800,485,slave=1);assert not w.isError(),w
r2=c.read_holding_registers(800,1,slave=1);assert not r2.isError() and r2.registers==[485],r2
c.close()
assert any(isinstance(x,tuple) and x[0].endswith('/save.php') for x in s.calls)
print('PASS: mocked ?s=1,0 parsed; 500=92 (9.2°C), 501=242 (24.2°C), 502=238 (23.8°C), 503=460 (46.0°C), unknown=0xFFFF.')
print('PASS: Modbus TCP read registers 500-502 and write/read register 800=485 (48.5°C); fake HTTP observed save.php POST (no live ISG contacted).')
