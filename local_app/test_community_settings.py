"""Portable first-run configuration, privacy validation, and LAN settings APIs."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import App, DEFAULTS, Server, handler, validate_device_settings, validate_weather_settings
from weather import geocode_city


class CommunitySettingsTests(unittest.TestCase):
    def test_defaults_are_public_test_placeholders(self):
        self.assertEqual(DEFAULTS['target'], '192.0.2.100')
        self.assertEqual(DEFAULTS['expected_id'], '0123456789ab')
        self.assertTrue(DEFAULTS['city'])

    def test_weather_and_device_validation(self):
        self.assertEqual(validate_weather_settings({'city':' 北京 ','latitude':'39.9042','longitude':116.4074}),
                         {'city':'北京','latitude':39.9042,'longitude':116.4074})
        self.assertEqual(validate_device_settings({'target':'192.0.2.44:32342','expected_id':'ABCDEF123456'}),
                         {'target':'192.0.2.44','expected_id':'abcdef123456'})
        for value in (
            {'city':'','latitude':0,'longitude':0},
            {'city':'x'*41,'latitude':0,'longitude':0},
            {'city':'北京','latitude':91,'longitude':0},
            {'city':'北京','latitude':0,'longitude':181},
            {'city':'北京\n上海','latitude':0,'longitude':0},
        ):
            with self.assertRaises(ValueError): validate_weather_settings(value)
        for target,expected in (('192.0.2.999','not-hex'),('example.test','abcdef123456'),('256.1.1.1','abcdef123456'),('192.0.2.44:0','abcdef123456')):
            with self.assertRaises(ValueError): validate_device_settings({'target':target,'expected_id':expected})

    def test_geocoder_bounds_and_formats_remote_results(self):
        payload={'results':[
            {'name':'北京','admin1':'北京市','country':'中国','country_code':'CN','timezone':'Asia/Shanghai','latitude':39.9042,'longitude':116.4074},
            {'name':'坏结果','latitude':91,'longitude':0},
        ]}
        with patch('weather.request_json',return_value=payload):
            result=geocode_city('北京')
        self.assertEqual(result,[{'name':'北京','label':'北京 · 北京市 · 中国','latitude':39.9042,'longitude':116.4074,
                                  'country_code':'CN','timezone':'Asia/Shanghai'}])

    def test_settings_endpoints_persist_without_leaking_private_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);config_path=path/'config.json'
            app=App(DEFAULTS,path,config_path)
            server=Server(('127.0.0.1',0),handler(app,0))
            port=server.server_address[1];server.RequestHandlerClass=handler(app,port)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(endpoint,body):
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                try:
                    connection.request('POST',endpoint,json.dumps(body),{'Content-Type':'application/json'})
                    response=connection.getresponse();return response.status,json.loads(response.read())
                finally: connection.close()
            try:
                with patch.object(app,'fetch') as fetch:
                    self.assertEqual(request('/api/weather/settings',{'city':'杭州','latitude':30.2741,'longitude':120.1551})[0],200)
                    fetch.assert_called_once()
                saved=json.loads(config_path.read_text())
                self.assertEqual((saved['city'],saved['latitude'],saved['longitude']),('杭州',30.2741,120.1551))
                with patch('app.geocode_city',return_value=[{'name':'杭州','label':'杭州 · 浙江省 · 中国','latitude':30.2741,'longitude':120.1551}]):
                    status,result=request('/api/weather/lookup',{'query':'杭州'})
                self.assertEqual(status,200);self.assertEqual(result['results'][0]['name'],'杭州')
                self.assertEqual(request('/api/device/settings',{'target':'192.0.2.44','expected_id':'abcdef123456'})[0],200)
                saved=json.loads(config_path.read_text())
                self.assertEqual((saved['target'],saved['expected_id']),('192.0.2.44','abcdef123456'))
                self.assertEqual(config_path.stat().st_mode & 0o777,0o600)
                self.assertEqual(request('/api/device/settings',{'target':'bad','expected_id':'abcdef123456'})[0],400)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':unittest.main()
