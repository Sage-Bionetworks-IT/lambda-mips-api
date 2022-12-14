import os

import boto3
import requests


class App:
    _mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
    _mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
    _mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

    ssm_client = None

    required_secrets = [ 'user', 'pass' ]

    def __init__(self):
        if App.ssm_client is None:
            App.ssm_client = boto3.client('ssm')

        self._ssm_secrets = None
        self._mips_org = None
        self.mips_dict = {}
        self.valid_routes = []

        try:
            self.valid_routes.append(os.environ['apiAllCostCenters'])
        except KeyError:
            raise Exception("The environment variable 'apiAllCostCenters' must be set.")

        try:
            self._mips_org = os.environ['MipsOrg']
        except KeyError:
            raise Exception("The environment variable 'MipsOrg' must be set.")

        try:
            self.ssm_path = os.environ['SsmPath']
        except KeyError:
            raise Exception("The environment variable 'SsmPath' must be set.")

    def collect_secrets(self):
        '''Collect secure parameters'''
        if self._ssm_secrets is None:
            self._ssm_secrets = {}

            params = App.ssm_client.get_parameters_by_path(
                Path=self.ssm_path,
                Recursive=True,
                WithDecryption=True,
            )

            if 'Parameters' in params:
                for p in params['Parameters']:
                    # strip leading path plus / char
                    if len(p['Name']) > len(self.ssm_path):
                        name = p['Name'][len(self.ssm_path)+1:]
                    else:
                        name = p['Name']
                    self._ssm_secrets[name] = p['Value']
                    print(f"Loaded secret: {name}")
            else:
                raise Exception(f"Invalid response from SSM client")

        for reqkey in App.required_secrets:
            if reqkey not in self._ssm_secrets:
                raise Exception(f"Missing required secure parameter: {reqkey}")

    def _collect_mips_data(self):
        '''Log into MIPS, get the chart of accounts, and log out'''

        try:
            # get mips access token
            mips_creds = {
                'username': self._ssm_secrets['user'],
                'password': self._ssm_secrets['pass'],
                'org': self._mips_org,
            }
            login_response=requests.post(
                App._mips_url_login,
                json=mips_creds,
            )
            login_response.raise_for_status()
            access_token = login_response.json()["AccessToken"]

            # Per Finance we filter the results taking just the records where segment=Program and status=A
            # There are about 150 records. We use a page size of 500 to get all the results in a single request.
            chart_params = {
                "filter[segmentId]":"Program",
                "filter[accountStatusId]":"A",
                "page[size]":"500"
            }
            chart_response=requests.get(
                App._mips_url_chart,
                chart_params,
                headers={"Authorization-Token":access_token},
            )
            chart_response.raise_for_status()
            accounts = chart_response.json()["data"]

            # save chart of accounts as a dict mapping code to title, e.g. { '990300': 'Platform Infrastructure' }
            for a in accounts:
                self.mips_dict[a['accountCodeId']] = a['accountTitle']

        except Exception as exc:
            print('Error interacting with mips')
            raise exc

        finally:
            # It's important to logout. Logging in a second time without logging out will lock us out of MIPS
            requests.post(
                App._mips_url_logout,
                headers={"Authorization-Token":access_token},
            )

    def get_mips_data(self, lookup):
        '''TODO: implement cache lookup'''
        if lookup not in self.valid_routes:
            raise Exception(f"Invalid request: {lookup}")

        if not self.mips_dict:
            self._collect_mips_data()

        return self.mips_dict
