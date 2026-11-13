import json
import socket
import threading
import time
import unittest

import requests
import proxy
from pymodbus.client import ModbusTcpClient
from pymodbus.server import StartTcpServer, ServerStop
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext


PAGE = """
<table>
<tr><td>AUSSENTEMPERATUR</td><td>-9,2 °C</td></tr>
<tr><td>VORLAUFISTTEMPERATUR WP</td><td>24,2 °C</td></tr>
<tr><td>RÜCKLAUFISTTEMPERATUR</td><td>23,8 °C</td></tr>
<tr><td>ISTTEMPERATUR</td><td>46,0 °C</td></tr>
</table>
<div class="calibration"><h3 class="title">SOLLTEMP. KOMFORT</h3>
<script>jsvalues['22']['val'] = '48,0';</script></div>
<div class="calibration"><h3 class="title">SOLLTEMPERATUR ECO</h3>
<script>jsvalues['23']['val'] = '42,0';</script></div>
"""


class Resp:
    text = PAGE

    def __init__(self, result=None):
        self.result = {'success': True} if result is None else result

    def raise_for_status(self):
        pass

    def json(self):
        if self.result == 'not-json':
            raise requests.exceptions.JSONDecodeError('invalid JSON', '', 0)
        return self.result


class FakeSession:
    def __init__(self):
        self.calls = []
        self.result = {'success': True}
        self.error = None

    def get(self, url, timeout=10):
        return Resp()

    def post(self, url, data, timeout=10):
        self.calls.append((url, json.loads(data['data'])))
        if self.error:
            raise self.error
        return Resp(self.result)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = proxy.Store()
        self.session = FakeSession()
        self.store.session = self.session
        self.store.update({'dhw_comfort': 48, 'dhw_eco': 42, 'mode': 2})

    def test_poll_and_signed_encoding(self):
        data = proxy.poll_once(session=self.session, base='http://fixture')
        self.store.update(data)
        self.assertEqual(self.store.getValues(500, 4), [(-92) & 0xffff, 242, 238, 460])
        self.assertEqual(self.store.getValues(800, 2), [480, 420])
        self.store.update({'dhw_upper_set': 55.5})
        self.assertEqual(self.store.getValues(810), [555])
        self.assertEqual(self.store.getValues(1000), [proxy.INVALID])
        self.assertEqual(proxy.encode_register('outdoor', None), proxy.INVALID)

    def test_independent_payloads_and_cache(self):
        for reg, key, field, raw in (
            (800, 'dhw_comfort', 'val22', 485),
            (801, 'dhw_eco', 'val23', 435),
        ):
            other = 801 if reg == 800 else 800
            previous = self.store.getValues(other)
            self.assertIsNone(self.store.setValues(reg, [raw]))
            self.assertEqual(self.session.calls[-1][1],
                             [{'name': field, 'value': str(raw / 10).replace('.', ',')}])
            self.assertTrue(self.session.calls[-1][0].endswith('/save.php'))
            self.assertEqual(self.store.getValues(reg), [raw])
            self.assertEqual(self.store.values_by_key[key], raw / 10)
            self.assertEqual(self.store.getValues(other), previous)

    def test_ranges_and_mode(self):
        for reg in (800, 801):
            for raw in (100, 600):
                self.store.setValues(reg, [raw])
                self.assertEqual(self.store.getValues(reg), [raw])
            for raw in (99, 601, 0xffff, -1, 65536):
                before = self.store.getValues(reg)
                calls = len(self.session.calls)
                with self.assertRaises(ValueError):
                    self.store.setValues(reg, [raw])
                self.assertEqual(self.store.getValues(reg), before)
                self.assertEqual(len(self.session.calls), calls)
        for mode in range(6):
            self.store.setValues(1500, [mode])
            self.assertEqual(self.store.values_by_key['mode'], mode)
            self.assertEqual(self.session.calls[-1][1],
                             [{'name': 'val1', 'value': str(mode)},
                              {'name': 'val1s', 'value': str(mode)}])
        with self.assertRaises(ValueError):
            self.store.setValues(1500, [6])
        self.assertEqual(self.store.getValues(1500), [5])

    def test_unsupported_and_multi_writes_do_not_post(self):
        for reg, values in ((500, [100]), (799, [1, 485]), (800, [485, 435])):
            before = self.store.getValues(reg, len(values))
            with self.assertRaises(ValueError):
                self.store.setValues(reg, values)
            self.assertEqual(self.store.getValues(reg, len(values)), before)
        self.assertEqual(self.session.calls, [])

    def test_failed_posts_preserve_registers_and_cache(self):
        for reg in (800, 801, 1500):
            for error in (requests.Timeout('timeout'), requests.HTTPError('HTTP 500'), None):
                results = ({'success': True},) if error else (
                    {'success': False}, {'success': False, 'warning': True},
                    {}, [], 'not-json',
                )
                for result in results:
                    self.session.error, self.session.result = error, result
                    before = self.store.getValues(reg)
                    cache = dict(self.store.values_by_key)
                    with self.assertRaises((requests.RequestException, RuntimeError)):
                        self.store.setValues(reg, [3 if reg == 1500 else 500])
                    self.assertEqual(self.store.getValues(reg), before)
                    self.assertEqual(self.store.values_by_key, cache)

    def test_modbus_tcp(self):
        context = ModbusServerContext(
            slaves=ModbusSlaveContext(hr=self.store, zero_mode=True), single=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = threading.Thread(target=StartTcpServer, kwargs={
            'context': context, 'address': ('127.0.0.1', port)}, daemon=True)
        server.start()
        client = ModbusTcpClient('127.0.0.1', port=port)
        try:
            deadline = time.monotonic() + 5
            while not client.connect():
                if time.monotonic() > deadline:
                    self.fail('Modbus server did not start')
                time.sleep(.05)
            self.store.update({'outdoor': -9.2})
            read = client.read_holding_registers(500, 1, slave=1)
            self.assertFalse(read.isError())
            self.assertEqual(int.from_bytes(read.registers[0].to_bytes(2, 'big'),
                                           'big', signed=True), -92)
            for reg, raw in ((800, 485), (801, 435), (1500, 3)):
                self.assertFalse(client.write_register(reg, raw, slave=1).isError())
                self.assertEqual(client.read_holding_registers(reg, 1, slave=1).registers, [raw])
            for reg, raw in ((800, 99), (801, 601), (1500, 6), (500, 100)):
                before = self.store.getValues(reg)
                response = client.write_register(reg, raw, slave=1)
                self.assertTrue(response.isError())
                self.assertEqual(response.exception_code, 4)
                self.assertEqual(self.store.getValues(reg), before)
            for error, result in ((requests.Timeout(), {'success': True}),
                                  (requests.HTTPError(), {'success': True}),
                                  (None, {'success': False}), (None, 'not-json')):
                self.session.error, self.session.result = error, result
                before = self.store.getValues(800)
                response = client.write_register(800, 500, slave=1)
                self.assertTrue(response.isError())
                self.assertEqual(response.exception_code, 4)
                self.assertEqual(client.read_holding_registers(800, 1, slave=1).registers, before)
            self.session.error, self.session.result = None, {'success': True}
            self.assertFalse(client.write_registers(800, [490], slave=1).isError())
            calls = len(self.session.calls)
            response = client.write_registers(800, [500, 450], slave=1)
            self.assertTrue(response.isError())
            self.assertEqual(response.exception_code, 4)
            self.assertEqual(len(self.session.calls), calls)
            self.assertEqual(self.store.getValues(800, 2), [490, 435])
        finally:
            client.close()
            ServerStop()
            server.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
