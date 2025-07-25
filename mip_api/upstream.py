import logging

from mip_api import util

import backoff
import requests
from requests.exceptions import RequestException
from urllib3.exceptions import RequestError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)

mip_url_login = "https://login.mip.com/api/v1/sso/mipadv/login"
mip_url_coa_segments = "https://api.mip.com/api/coa/segments"
mip_url_coa_accounts = "https://api.mip.com/api/coa/segments/accounts"
mip_url_logout = "https://api.mip.com/api/security/logout"
mip_url_current_balance = (
    "https://api.mip.com/api/model/CBODispBal/methods/GetAccountBalances"
)


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _login(creds):
    """
    Wrap login request with backoff decorator, using exponential backoff
    and running for at most 11 seconds. With a connection timeout of 4
    seconds, this allows two attempts.
    """
    timeout = 4
    LOG.info("Logging in to upstream API")

    login_response = requests.post(
        mip_url_login,
        json=creds,
        timeout=timeout,
    )
    login_response.raise_for_status()
    token = login_response.json()["AccessToken"]
    return token


@backoff.on_exception(backoff.fibo, (RequestError, RequestException), max_time=28)
def _logout(access_token):
    """
    Wrap logout request with backoff decorator, using fibonacci backoff
    and running for at most 28 seconds. With a connection timeout of 6
    seconds, this allows three attempts.

    Prioritize spending time logging out over the other requests because
    failing to log out after successfully logging in will lock us out of
    the API; but CloudFront will only wait a maximum of 60 seconds for a
    response from this lambda.
    """
    timeout = 6
    LOG.info("Logging out of upstream API")

    requests.post(
        mip_url_logout,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _get_segment_id(access_token, segment_name):
    """
    Wrap the request for chart segment IDs with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return the ID of the "Program" segment needed for filtering.
    """
    timeout = 4
    LOG.info("Getting chart segments")

    # get segments from api
    segment_response = requests.get(
        mip_url_coa_segments,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )
    segment_response.raise_for_status()
    json_response = segment_response.json()
    LOG.debug(f"Raw segment json: {json_response}")

    # get the segment ID for the "Program" segment
    seg_id = None
    for segment in json_response["COA_SEGID"]:
        if segment["TITLE"] == segment_name:
            seg_id = segment["COA_SEGID"]
            break

    if seg_id is None:
        raise ValueError(f"Segment {segment_name} not found")

    return seg_id


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _get_chart_segment(access_token, seg_id, hide_inactive):
    """
    Wrap the request for chart of accounts with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return results for active accounts in the program segment.
    """
    timeout = 4
    LOG.info("Getting chart of accounts")

    # get segments from api
    account_response = requests.get(
        mip_url_coa_accounts,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
    )
    account_response.raise_for_status()
    json_response = account_response.json()
    LOG.debug(f"Raw account json: {json_response}")

    accounts = {}
    for account in json_response["COA_SEGID"]:
        if account["COA_SEGID"] == seg_id:
            if hide_inactive and account["COA_STATUS"] != "A":
                LOG.info(f"Hiding inactive account: {account['COA_TITLE']}")
                continue
            accounts[account["COA_CODE"]] = account["COA_TITLE"]

    LOG.info(f"Chart of accounts: {accounts}")
    return accounts


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _get_balance(access_token, period_from, period_to):
    timeout = 4
    LOG.info("Getting balances")

    # copied from chrome dev tools while clicking through the web ui
    body = {
        "BOInformation": {
            "constructor": {},
            "ModelFields": {
                "fields": [
                    {"DISPBAL_DATEFROM": period_from},
                    {"DISPBAL_DATETO": period_to},
                ],
                "DISPBAL_SEGINFO": [
                    {
                        "fields": [
                            {"GRID_PHY_ID": 0},
                            {"FILTER_SELECT": True},
                            {"FILTER_ORDER": -1},
                            {"FILTER_FIX": False},
                            {"FILTER_DATATYPE": 10},
                            {"FILTER_FIELDTYPE": 40},
                            {"FILTER_ITEM": "GL"},
                            {"FILTER_OPERATOR": "<>"},
                            {"FILTER_CRITERIA1": "<Blank>"},
                            {"FILTER_CRITERIA2": ""},
                        ]
                    }
                ],
            },
        },
        "MethodParameters": {"strJson": '{"level":1}'},
    }

    api_response = requests.post(
        mip_url_current_balance,
        headers={"Authorization-Token": access_token},
        timeout=timeout,
        json=body,
    )
    api_response.raise_for_status()
    json_response = api_response.json()
    LOG.debug(f"Raw balance documents json: {json_response}")

    balance = json_response
    return balance


def get_chart(org_name, secrets, segment, hide_inactive):
    """
    Log into MIPS, get the chart of accounts, and log out
    """

    chart_dict = {}
    access_token = None

    mip_creds = {
        "username": secrets["user"],
        "password": secrets["pass"],
        "org": org_name,
    }

    try:
        # get mip cloud access token
        access_token = _login(mip_creds)

        # get the program segment id
        segment_id = _get_segment_id(access_token, segment)

        # get the chart of program accounts
        chart_dict = _get_chart_segment(access_token, segment_id, hide_inactive)

    except Exception as exc:
        LOG.exception("Error interacting with upstream API")

    finally:
        # It's important to logout. Logging in a second time without
        # logging out will lock us out of the upstream API
        if access_token is not None:
            try:
                _logout(access_token)
            except Exception as exc:
                LOG.exception("Error logging out")

    return chart_dict


def trial_balances(org_name, secrets, when=None):
    bal_dict = {}
    access_token = None

    start_str, end_str = util.target_period(when)

    mip_creds = {
        "username": secrets["user"],
        "password": secrets["pass"],
        "org": org_name,
    }

    try:
        # get mip access token
        access_token = _login(mip_creds)
        bal_dict = _get_balance(access_token, start_str, end_str)
    except Exception as exc:
        LOG.exception(exc)
    finally:
        # It's important to logout. Logging in a second time without
        # logging out will lock us out of the upstream API
        try:
            _logout(access_token)
        except Exception as exc:
            LOG.exception("Error logging out")

    bal_dict["period_from"] = start_str
    bal_dict["period_to"] = end_str

    LOG.debug(f"Balance dict: {bal_dict}")

    return bal_dict
