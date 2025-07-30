import logging

from mip_api import s3, upstream

import csv
import io

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


def get_balances(org_name, secrets, bucket, path, when=None):
    """
    Get trial balances from MIP Cloud and cache a successful response in S3.

    Parameters
    ----------
    org_name : str
        MIP Cloud organization name

    secrets : dict
        MIP Cloud authentication credentials

    bucket : str
        S3 bucket name

    path : str
        S3 object path

    when : str
        Activity period target date in ISO 8601 format (YYYY-MM-DD)
    """
    LOG.info("Read balances from upstream API")
    upstream_dict = upstream.trial_balances(org_name, secrets, when)
    LOG.debug(f"Upstream API response: {upstream_dict}")

    bal_dict = s3.cache(upstream_dict, bucket, path)
    return bal_dict


def process_balance(bal_dict, coa_dict):
    """
    Process upstream API response into a list of lists representing
    rows in a CSV file.

    Parameters
    ----------
    bal_dict : dict
        Upstream API response

    coa_dict : dict
        Chart of accounts

    Returns
    -------
    list
        List of lists representing rows in CSV file.
        Headers:
            'AccountNumber', 'AccountName', 'PeriodStart', 'PeriodEnd',
            'StartBalance', 'Activity', 'EndBalance'

    """

    # check for success
    if "executionResult" not in bal_dict:
        LOG.error(f"No execution result found: '{bal_dict}'")
        raise KeyError("No 'executionResult' key found")

    result = bal_dict["executionResult"]
    if result != "SUCCESS":
        LOG.error(f"Execution result is not 'SUCCESS': '{result}'")
        raise ValueError("Execution result is not 'SUCCESS'")

    # collate api response into a dict
    _data = {}

    _detail = []
    for k, v in bal_dict["extraInformation"].items():
        if k != "Level1":
            LOG.error(f"Unexpected key (not 'Level1'): {k}")
            raise KeyError("No 'Level1' key found")
        else:
            _detail = v

    for d in _detail:
        account_id = d["DBDETAIL_SUM_SEGMENT_N0"]
        if account_id not in _data:
            _data[account_id] = {}

        if d["DBDETAIL_SUM_TYPE"] == 1:
            _data[account_id]["balance_start"] = d["DBDETAIL_SUM_POSTEDAMT"]
        elif d["DBDETAIL_SUM_TYPE"] == 2:
            _data[account_id]["activity"] = d["DBDETAIL_SUM_POSTEDAMT"]
        elif d["DBDETAIL_SUM_TYPE"] == 3:
            _data[account_id]["balance_end"] = d["DBDETAIL_SUM_POSTEDAMT"]
        else:
            LOG.error(f"Unknown balance type: {d['DBDETAIL_SUM_DESC']}")

    LOG.debug(f"Raw internal balance dict: {_data}")

    # List of rows in CSV
    out_rows = []

    # Add header row
    headers = [
        "AccountNumber",
        "AccountName",
        "PeriodStart",
        "PeriodEnd",
        "StartBalance",
        "Activity",
        "EndBalance",
    ]
    out_rows.append(headers)

    # Generate rows from input dict
    for k, v in _data.items():
        if k not in coa_dict:
            LOG.error(f"Key {k} not found in chart of accounts")
            LOG.debug(f"List of accounts: {coa_dict.keys()}")
            continue
        name = coa_dict[k]

        row = [
            k,
            name,
            bal_dict["period_from"],
            bal_dict["period_to"],
            v["balance_start"],
            v["activity"],
            v["balance_end"],
        ]
        out_rows.append(row)

    return out_rows


def format_csv(bal_dict, coa_dict):
    """
    Process upstream API response into string contents of a CSV file.

    Parameters
    ----------
    bal_dict : dict
        Upstream API response

    coa_dict : dict
        Chart of accounts

    Returns
    -------
    str
        String contents of CSV file.
    """
    csv_out = io.StringIO()
    csv_writer = csv.writer(csv_out)

    csv_rows = process_balance(bal_dict, coa_dict)
    for row in csv_rows:
        csv_writer.writerow(row)

    return csv_out.getvalue()
