from mips_api.cache import S3Cache

import json
import os

import boto3
import requests


class App:
    s3_cache = None
    ssm_client = None

    # required SSM secrets
    _required_secrets = [ 'user', 'pass', 'org' ]

    # All known cached files
    _all_cache_paths = [
        '/catalog/ProgramCodes.json',
        '/catalog/ProgramCodesOther.json',
        '/costs/CategoryRules.yaml',
    ]

    _mips_url_login = 'https://login.abilaonline.com/api/v1/sso/mipadv/login'
    _mips_url_chart = 'https://mipapi.abilaonline.com/api/v1/maintain/chartofaccounts'
    _mips_url_logout = 'https://mipapi.abilaonline.com/api/security/logout'

    # distinguish program codes from program codes other
    _main_program_codes = [
        '000000',  # NO PROGRAM
        '000001',  # OTHER
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

    # ignore these codes entirely
    _omit_program_codes = [
        '999900',  # unfunded
        '999800',  # salary cap
        '999700',  # long term leave
        '990500',  # program management
    ]

    # these codes are no longer active in MIPS, but we need to keep them around
    _legacy_program_codes = [
        '30144',
    ]

    _rule_info = {}

    def __init__(self):
        try:
            self.SSM_PATH = os.environ['SsmPath']
        except KeyError:
            raise Exception("The environment variable 'SsmPath' must be set.")

        if App.ssm_client is None:
            App.ssm_client = boto3.client('ssm')

        # dict mapping program codes to program titles
        self._mips_dict = {}

        if App.s3_cache is None:
            App.s3_cache = S3Cache()

        self._ssm_secrets = self._get_ssm_parameters()

    def _get_ssm_parameters(self):
        '''Get secure parameters from SSM'''
        params = App.ssm_client.get_parameters_by_path(
            Path=self.SSM_PATH,
            Recursive=True,
            WithDecryption=True,
        )

        secrets = {}
        if 'Parameters' in params:
            for p in params['Parameters']:
                # strip leading path plus / char
                if len(p['Name']) > len(self.SSM_PATH):
                    name = p['Name'][len(self.SSM_PATH)+1:]
                else:
                    name = p['Name']
                secrets[name] = p['Value']
                print(f"Loaded secret: {name}")
        return secrets

    def _get_mips_data(self):
        '''Log into MIPS, get the chart of accounts, and log out'''

        # get mips access token
        mips_creds = {
            'username': self._ssm_secrets['user'],
            'password': self._ssm_secrets['pass'],
            'org': self._ssm_secrets['org'],
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

        # It's important to logout. Logging in a second time without logging out will lock us out of MIPS
        requests.post(
            App._mips_url_logout,
            headers={"Authorization-Token":access_token},
        )

        # save chart of accounts as a dict mapping code to title, e.g. { '990300': 'Platform Infrastructure' }
        for a in accounts:
            self._mips_dict[a['accountCodeId']] = a['accountTitle']

    def _generate_category_rules(self):
        ''' '''
        return 'test category rule'

    def _generate_program_codes(self):
        '''Transform codes into CostCenter tag format for primary Program Codes'''
        code_list = [
            f"{v} / {k}"
            for k,v in self._mips_dict.items()
            if k in App._main_program_codes
        ]
        print(f"Generated codes: {code_list}")
        return code_list

    def _generate_program_codes_other(self):
        '''Transform codes into CostCenter tag format for secondary Program Codes'''
        skip_list = []
        skip_list.extend(App._main_program_codes)
        skip_list.extend(App._omit_program_codes)

        # ignore inactive codes (shorter code length), except for some legacy codes
        active_codes = {}
        for k, v in self._mips_dict.items():
            if len(k) > 5 or k in App._legacy_program_codes:
                active_codes[k] = v
        print(f"Active program codes: {active_codes}")

        code_list = [
            f"{v} / {k}"
            for k,v in active_codes.items()
            if k not in skip_list
        ]

        print(f"Generated codes (other): {code_list}")
        return code_list

    def _process_mips_data(self, path):
        '''Transform chart of accounts into desired format'''
        if path not in App._all_cache_paths:
            raise Exception(f"Invalid request: {path}")

        if not self._mips_dict:
            # Get data from mips if needed
            self._get_mips_data()

        if path == '/costs/CategoryRules.yaml':
            return self._generate_category_rules()

        elif path == '/catalog/ProgramCodes.json':
            return self._generate_program_codes()

        elif path == '/catalog/ProgramCodesOther.json':
            return self._generate_program_codes_other()

    def get_mips_data(self, path):
        '''Collect requested data and write to cache'''
        mips_data = self._process_mips_data(path)

        try:
            App.s3_cache.put_cache(path, json.dumps(mips_data))
        except Exception as exc:
            print(f"Cache write exception: {exc}")

        return mips_data

    def has_secrets(self):
        '''Check for required secure parameters'''
        for reqkey in App._required_secrets:
            if reqkey not in self._ssm_secrets:
                print(f"Missing required secure parameter: {reqkey}")
                return False
        return True

    def refresh_cache(self):
        '''Refresh all cache files'''
        retval = True
        for path in App._all_cache_paths:
            try:
                data = self._process_mips_data(path)
                App.s3_cache.put_cache(path, json.dumps(data))
            except Exception as exc:
                print(exc)
                retval = False
        return retval
