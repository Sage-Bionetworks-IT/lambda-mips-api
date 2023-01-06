import json
import os

import boto3
import requests


class App:
    _mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
    _mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
    _mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

    # add these meta codes as valid `CostCenter` values
    _extra_program_codes = {
        '000000':  'No Program',
        '000001':  'Other',
    }

    # ignore these active codes
    _omit_program_codes = [
        '999900',  # unfunded
        '999800',  # salary cap
        '999700',  # long term leave
        '990500',  # program management
    ]

    ssm_client = None

    required_secrets = [ 'user', 'pass' ]

    def _get_os_var(self, varnam):
        try:
            return os.environ[varnam]
        except KeyError as exc:
            raise Exception(f"The environment variable '{varnam}' must be set")

    def __init__(self):
        if App.ssm_client is None:
            App.ssm_client = boto3.client('ssm')

        self._ssm_secrets = None
        self._mips_org = None

        self.mips_dict = {}
        self.params = {}
        self.api_routes = {}

        self._mips_org = self._get_os_var('MipsOrg')
        self.ssm_path = self._get_os_var('SsmPath')

        api_routes = [
            'apiAllAccounts',
            'apiTagValues',
        ]
        for route_name in api_routes:
            self.api_routes[route_name] = self._get_os_var(route_name)

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

        access_token = None
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

    def _collect_params(self, event_params):
        '''Process query string parameters'''

        if 'limit' in event_params:
            try:
                self.params['limit'] = int(event_params['limit'])
            except TypeError as exc:
                print('limit parameter must be an integer')
                raise exc

    def _mips_dict_json(self):
        '''
        Transform the full chart of accounts into JSON
        '''
        return json.dumps(self.mips_dict, indent=2)

    def _service_catalog_json(self):
        '''
        Transform data into a format for service catalog tag validation

        Include values for both the `CostCenter` and `CostCenterOther` tags.

        Returns
            A JSON string representing an array of strings in the format `{Program Name} / {Program Code}`
        '''

        data = []
        found = []

        for code, name in App._extra_program_codes.items():
            data.append(f"{name} / {code}")

        for code, name in self.mips_dict.items():
            if len(code) > 5: # inactive codes have 5 digits
                short = code[:6]  # ignore the last two digits on active codes
                if short not in App._omit_program_codes:
                    if short not in found:
                        title = f"{name} / {short}"
                        data.append(title)
                        found.append(short)

        result = data
        if 'limit' in self.params:
            limit = self.params['limit']
            result = data[0:limit]

        return json.dumps(result, indent=2)

    def valid_routes(self):
        '''
        List all valid routes
        '''
        return self.api_routes.values()

    def _get_mips_data(self, lookup):
        '''
        Process MIPS data into requested format
        '''

        if lookup == self.api_routes['apiAllAccounts']:
            return self._mips_dict_json()

        if lookup == self.api_routes['apiTagValues']:
            return self._service_catalog_json()

    def get_mips_data(self, lookup, params):
        '''
        Entry point for retrieving data

        Returns
            The body of the requested object
        '''

        # Collect query string parameters
        if params:
            self._collect_params(params)

        # Query MIPS if needed
        if not self.mips_dict:
            self._collect_mips_data()

        # Collect requested data
        data = self._get_mips_data(lookup)

        # Return the object body
        return data
