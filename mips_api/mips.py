import json
import os

import boto3
import requests


class App:
    _mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
    _mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
    _mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

    # distinguish `CostCenter` from `CostCenterOther`
    _main_program_codes = [
        '101300',  # CSBC - NCI
        '101400',  # AoU - Scripps
        '101600',  # NIH-ITCR
        '112501',  # Mobile Toolbox Project Core
        '119400',  # Depression-Emory Wingo
        '120100',  # HTAN-DFCI
        '121700',  # HTAN Supp DFCI
        '122000',  # INCLUDE - CHOP
        '122100',  # NF - DoD w/UCF
        '122300',  # Emory Diversity Cohorts
        '312000',  # Genie-AACR
        '314900',  # iAtlas 3
        '401900',  # Digital Accelerators-ADDF
        '506800',  # NLP CH - Celgene
        '613500',  # Psorcast Pscrosis App
        '990100',  # General + Administrative
        '990300',  # Platform Infrastructure
        '990400',  # Governance
    ]

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

    # inactive codes that we still need
    _legacy_program_codes = [
        '30144',
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
        self.valid_routes = {}

        self._mips_org = self._get_os_var('MipsOrg')
        self.ssm_path = self._get_os_var('SsmPath')

        api_routes = [
            'apiAllCostCenters',
            'apiProgramCodes',
            'apiProgramCodesAll',
        ]
        for route_name in api_routes:
            self.valid_routes[route_name] = self._get_os_var(route_name)

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

    def _mips_dict_json(self):
        '''
        Transform the full chart of accounts into JSON
        '''
        return json.dumps(self.mips_dict, indent=2)

    def _service_catalog_json(self):
        '''
        Transform data into a format for service catalog tag options

        Include only values for the `CostCenter` tag, excluding values for the `CostCenterOther` tag.

        Returns
            A JSON string representing an array of strings in the format `{Program Name} / {Program Code}`
        '''

        data = []

        for code, name in self.mips_dict.items():
            short = code[:6]  # ignore the last two digits on active codes

            if short in App._main_program_codes:
                title = f"{name} / {short}"
                if title not in data:
                    data.append(title)

        for code, name in App._extra_program_codes.items():
            data.append(f"{name} / {code}")

        return json.dumps(data, indent=2)

    def _service_catalog_all_json(self):
        '''
        Transform data into a format for service catalog tag validation

        Include values for both the `CostCenter` and `CostCenterOther` tags.

        Returns
            A JSON string representing an array of strings in the format `{Program Name} / {Program Code}`
        '''

        data = []

        for code, name in App._extra_program_codes.items():
            data.append(f"{name} / {code}")

        for code, name in self.mips_dict.items():
            if code in App._legacy_program_codes or len(code) > 5:
                short = code[:6]  # ignore the last two digits on active codes
                if short not in App._omit_program_codes:
                    title = f"{name} / {short}"
                    if title not in data:
                        data.append(title)

        return json.dumps(data, indent=2)

    def valid_routes(self):
        '''
        List all valid routes
        '''
        return self.valid_routes.values()

    def _get_mips_data(self, lookup):
        '''
        Process MIPS data into requested format
        '''

        if lookup == self.valid_routes['apiAllCostCenters']:
            return self._mips_dict_json()

        if lookup == self.valid_routes['apiProgramCodes']:
            return self._service_catalog_json()

        if lookup == self.valid_routes['apiProgramCodesAll']:
            return self._service_catalog_all_json()

    def get_mips_data(self, lookup):
        '''
        Entry point for retrieving data

        Returns
            The body of the requested object
        '''

        # Query MIPS if needed
        if not self.mips_dict:
            self._collect_mips_data()

        # Collect requested data
        data = self._get_mips_data(lookup)

        # Return the object body
        return data
