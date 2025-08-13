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


@backoff.on_exception(backoff.expo, (RequestError, RequestException), max_time=11)
def _login(creds):
    """
    Login to upstream API.

    Wrap login request with backoff decorator, using exponential backoff
    and running for at most 11 seconds. With a connection timeout of 4
    seconds, this allows two attempts.

    Parameters
    ----------
    creds : dict
        Authentication credentials to log in to upstream API.

    Returns
    -------
    str
        Authorization token.
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
    Logout from upstream API.

    Wrap logout request with backoff decorator, using fibonacci backoff
    and running for at most 28 seconds. With a connection timeout of 6
    seconds, this allows three attempts.

    Prioritize spending time logging out over the other requests because
    failing to log out after successfully logging in will lock us out of
    the API; but CloudFront will only wait a maximum of 60 seconds for a
    response from this lambda.

    Parameters
    ----------
    access_token : str
        Authorization token.

    Returns
    -------
    None
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
    Get segment ID for the given segment name.

    Wrap the request for chart segment IDs with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return the ID of the "Program" segment needed for filtering.

    Parameters
    ----------
    access_token : str
        Authorization token.

    segment_name : str
        Segment name.

    Returns
    -------
    int
        Segment ID.
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

    # The upstream API re-uses the key `COA_SEGID` both as a
    # top-level key mapped to the list of segment definitions,
    # and also as a sub-key within each segment definition to
    # provide the specific segment ID.
    # See test_handler.mock_segments for an example API response.
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
    Get chart of accounts for the given segment, optionally excluding
    inactive accounts.

    Wrap the request for chart of accounts with backoff decorator, using
    exponential backoff and running for at most 11 seconds. With a
    connection timeout of 4 seconds, this allows two attempts.
    Only return results for active accounts in the program segment.

    Parameters
    ----------
    access_token : str
        Authorization token.

    seg_id : int
        Segment ID.

    hide_inactive : bool
        Exclude inactive accounts.

    Returns
    -------
    dict
       Dictionary mapping account codes to their names.
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

    # The upstream API re-uses the key `COA_SEGID` as both a top-level
    # key mapped to a list of account definitions, and also as a sub-key
    # within each account definition to provide the segment
    # See test_handler.mock_accounts for an example API response.
    accounts = {}
    for account in json_response["COA_SEGID"]:
        if account["COA_SEGID"] == seg_id:
            title = account["COA_TITLE"]
            if hide_inactive and account["COA_STATUS"] != "A":
                LOG.info(f"Hiding inactive account: {title}")
                continue
            accounts[account["COA_CODE"]] = title

    LOG.info(f"Chart of accounts: {accounts}")
    return accounts


def get_chart(org_name, secrets, segment, hide_inactive):
    """
    Log into MIPS, get the chart of accounts, and log out

    Parameters
    ----------
    org_name : str
        MIP Cloud organization name.

    secrets : dict
        MIP Cloud authentication credentials.

    segment : str
        Segment name. Allowed values: ['Program', 'GL']

    hide_inactive : bool
        Exclude inactive accounts.

    Returns
    -------
    dict
        Dictionary mapping account codes to their names.
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
