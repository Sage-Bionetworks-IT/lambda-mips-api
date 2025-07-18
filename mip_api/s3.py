import json
import logging

import boto3


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)

s3_client = None


def _read(bucket, path):
    """
    Read MIP response from S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    data = s3_client.get_object(Bucket=bucket, Key=path)
    return json.loads(data["Body"].read())


def _write(data, bucket, path):
    """
    Write MIP response to S3 cache object
    """
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")

    body = json.dumps(data)
    s3_client.put_object(Bucket=bucket, Key=path, Body=body)


def cache(upstream_data, bucket, path):
    """
    Implement a write-through cache of successful responses to tolerate long-term faults in the upstream
    API.

    A successful API response will be stored in S3 indefinitely, to be retrieved
    and used in the case of an upstream API failure.

    The S3 bucket has versioning enabled for disaster recovery, but this means
    that every PUT request will create a new S3 object. In order to minimize
    the number of objects in the bucket, read the cache value on every run and
    only update the S3 object if it changes.
    """

    # always read cached value
    LOG.info("Read cached response from S3")
    cache_data = None
    try:
        cache_data = _read(bucket, path)
        LOG.debug(f"Cached API response: {cache_data}")
    except Exception as exc:
        LOG.exception("S3 read failure")

    if upstream_data:
        # if we received a non-empty response from the upstream API, compare it
        # to our cached response and update the S3 write-through cache if needed
        if upstream_data == cache_data:
            LOG.debug("No change in response data")
        else:
            # store write-through cache
            LOG.info("Write updated response data to S3")
            try:
                _write(upstream_data, bucket, path)
            except Exception as exc:
                LOG.exception("S3 write failure")
        out_data = upstream_data
    else:
        # no response (or an empty response) from the upstream API,
        # rely on the response cached in S3.
        out_data = cache_data

    if not out_data:
        # make sure we don't return an empty value
        raise ValueError("No valid response found")

    return out_data
