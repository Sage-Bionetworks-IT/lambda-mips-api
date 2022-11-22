from mips_api import cache

import datetime
import io
import os

import boto3
import pytest
from botocore.stub import Stubber


def test_cache(mocker):
    '''Test cache.S3Cache object'''

    test_key = 'test/key'
    test_bucket = 'testBucket'
    cache_body = 'cache content'

    # stub s3 client
    s3 = boto3.client('s3')
    cache.S3Cache.s3_client = s3

    with Stubber(s3) as stub_s3:

        # init failure: no bucket name
        with pytest.raises(Exception):
            s3_cache = cache.S3Cache()

        # add needed env var
        os.environ['CacheBucketName'] = test_bucket

        # init failure: invalid expiration value
        os.environ['CacheBucketExpirationDays'] = 'NaN'
        with pytest.raises(Exception):
            s3_cache = cache.S3Cache()

        # add valid env var
        os.environ['CacheBucketExpirationDays'] = '3'

        # create object under test
        s3_cache = cache.S3Cache()

        # test cache put + hit
        stub_s3.add_response('put_object', {})
        s3_cache.put_cache(test_key, cache_body)
        stub_s3.assert_no_pending_responses()

        cache_obj = {
            'LastModified': datetime.datetime.utcnow(),
            'Body': io.BytesIO(cache_body.encode()),
        }
        stub_s3.add_response('get_object', cache_obj)
        cache_hit = s3_cache.get_cache(test_key)

        assert cache_hit == cache_body
        stub_s3.assert_no_pending_responses()

        # test cache miss with exception
        stub_s3.add_client_error('get_object', service_error_code='NoSuchKey')
        with pytest.raises(Exception):
            s3_cache.get_cache(test_key)
        stub_s3.assert_no_pending_responses()

        # test expired cache
        cache_time = datetime.datetime.utcnow() - datetime.timedelta(days=366)  # over a year
        cache_obj = {
            'LastModified': cache_time,
            'Body': io.BytesIO(cache_body.encode()),
        }
        stub_s3.add_response('get_object', cache_obj)
        with pytest.raises(Exception):
            s3_cache.get_cache(test_key)
        stub_s3.assert_no_pending_responses()

        # test cache purge
        purge_list = {
            'Contents': [ {'Key': test_key}, ]
        }
        delete_param = {
            'Bucket': test_bucket,
            'Delete': {
                'Objects': [ {'Key': test_key}, ]
            }
        }

        stub_s3.add_response('list_objects', purge_list)
        stub_s3.add_response('delete_objects', {}, delete_param)
        s3_cache.purge_cache()
        stub_s3.assert_no_pending_responses()
