import logging

import boto3

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)

# This is global so that it can be stubbed in test.
# Because it's global, its value will be retained
# in the lambda environment and re-used on warm runs.
ssm_client = None


def get_secrets(ssm_path):
    """Collect secure parameters from SSM"""

    # create boto client
    global ssm_client
    if ssm_client is None:
        ssm_client = boto3.client("ssm")

    # object to return
    ssm_secrets = {}

    # get secret parameters from ssm
    params = ssm_client.get_parameters_by_path(
        Path=ssm_path,
        Recursive=True,
        WithDecryption=True,
    )
    if "Parameters" in params:
        for p in params["Parameters"]:
            # strip leading path plus / char
            if len(p["Name"]) > len(ssm_path):
                name = p["Name"][len(ssm_path) + 1 :]
            else:
                name = p["Name"]
            ssm_secrets[name] = p["Value"]
            LOG.info(f"Loaded secret: {name}")
    else:
        LOG.error(f"Invalid response from SSM client")
        raise Exception

    for reqkey in ["user", "pass"]:
        if reqkey not in ssm_secrets:
            raise Exception(f"Missing required secure parameter: {reqkey}")

    return ssm_secrets
