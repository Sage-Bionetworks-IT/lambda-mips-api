import logging

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
def _get_program_segment(access_token):
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
        if segment["TITLE"] == "Program":
            seg_id = segment["COA_SEGID"]
            break

    if seg_id is None:
        raise ValueError("Program segment not found")

    return seg_id


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _get_chart(access_token, seg_id):
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
        # require "Program" segment and "A" status
        if account["COA_SEGID"] == seg_id and account["COA_STATUS"] == "A":
            accounts[account["COA_CODE"]] = account["COA_TITLE"]

    LOG.info(f"Chart of accounts: {accounts}")
    return accounts


def program_chart(org_name, secrets):
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
        program_id = _get_program_segment(access_token)

        # get the chart of program accounts
        chart_dict = _get_chart(access_token, program_id)

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
