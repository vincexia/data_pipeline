# -*- coding: utf-8 -*-
# Copyright 2016 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import absolute_import
from __future__ import unicode_literals

import random
import time
from contextlib import contextmanager
from datetime import datetime

import mock
import pytest
import pytz
import simplejson
from bravado.exception import HTTPError
from requests.exceptions import ConnectionError
from requests.exceptions import ReadTimeout

from data_pipeline.config import get_config
from data_pipeline.schematizer_clientlib.models.data_source_type_enum import \
    DataSourceTypeEnum
from data_pipeline.schematizer_clientlib.models.meta_attr_namespace_mapping \
    import MetaAttributeNamespaceMapping
from data_pipeline.schematizer_clientlib.models.meta_attr_source_mapping \
    import MetaAttributeSourceMapping
from data_pipeline.schematizer_clientlib.models.namespace import Namespace
from data_pipeline.schematizer_clientlib.models.source import Source
from data_pipeline.schematizer_clientlib.models.target_schema_type_enum import \
    TargetSchemaTypeEnum
from data_pipeline.schematizer_clientlib.schematizer import SchematizerClient


class SchematizerClientTestBase(object):

    @pytest.fixture
    def schematizer(self, containers):
        return SchematizerClient()

    def attach_spy_on_api(self, client, resource_name, api_name):
        # We replace what the client is actually returning instead of just patching
        # since the client and the decorator both create new attributes on every call
        # to __getattr__, see:
        # https://github.com/Yelp/swagger_zipkin/blob/master/swagger_zipkin/zipkin_decorator.py
        # (__getattr__ of ZipkinClientDecorator)
        # https://github.com/Yelp/bravado/blob/master/bravado/client.py
        # (__getattr__ of ResourceDecorator)
        resource = getattr(client, resource_name)
        spied_callable_operation = getattr(resource, api_name)

        def attach_spy(*args, **kwargs):
            return spied_callable_operation(*args, **kwargs)

        setattr(client, resource_name, resource)
        return mock.patch.object(
            resource, api_name, side_effect=attach_spy
        )

    def _get_creation_timestamp(self, created_at):
        zero_date = datetime.fromtimestamp(0, created_at.tzinfo)
        return long((created_at - zero_date).total_seconds())

    def _get_created_after(self, created_at=None):
        day_one = (2015, 1, 1, 19, 10, 26, 0)
        tzinfo = created_at.tzinfo if created_at else pytz.utc
        return datetime(*day_one, tzinfo=tzinfo)

    @pytest.fixture(scope='class')
    def yelp_namespace_name(self):
        return 'yelp_{0}'.format(random.random())

    @pytest.fixture(scope='class')
    def aux_namespace(self):
        return 'aux_{0}'.format(random.random())

    @pytest.fixture(scope='class')
    def biz_src_name(self):
        return 'biz_{0}'.format(random.random())

    @pytest.fixture(scope='class')
    def biz_src_resp(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name
        ).topic.source

    @pytest.fixture(scope='class')
    def usr_src_name(self):
        return 'user_{0}'.format(random.random())

    @pytest.fixture(scope='class')
    def cta_src_name(self):
        return 'cta_{0}'.format(random.random())

    @pytest.fixture(scope='class')
    def biz_topic_resp(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name).topic

    @property
    def source_owner_email(self):
        return 'bam+test@yelp.com'

    @property
    def note(self):
        return 'note'

    def get_new_name(self, prefix):
        return '{}_{}'.format(prefix, random.random())

    def _get_client(self):
        """This is a method instead of a property.  Pytest was accessing this
        attribute before setting up fixtures, resulting in this code failing
        since the clientlib hadn't yet been reconfigured to access the
        schematizer container.
        """
        return get_config().schematizer_client

    def _register_avro_schema(
        self,
        namespace,
        source,
        schema_json=None,
        **overrides
    ):
        schema_json = schema_json or {
            'type': 'record',
            'name': source,
            'namespace': namespace,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'foo'}]
        }
        params = {
            'namespace': namespace,
            'source': source,
            'source_owner_email': self.source_owner_email,
            'schema': simplejson.dumps(schema_json),
            'contains_pii': False
        }
        if overrides:
            params.update(**overrides)
        return self._get_client().schemas.register_schema(body=params).result()

    def _create_note(self, reference_id, reference_type):
        note = {
            'reference_type': reference_type,
            'reference_id': reference_id,
            'note': self.note,
            'last_updated_by': self.source_owner_email
        }
        return self._get_client().notes.create_note(body=note).result()

    def _get_schema_by_id(self, schema_id):
        return self._get_client().schemas.get_schema_by_id(
            schema_id=schema_id
        ).result()

    def _assert_schema_values(self, actual, expected_resp):
        attrs = (
            'schema_id',
            'base_schema_id',
            'status',
            'primary_keys',
            'created_at',
            'updated_at'
        )
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)
        assert actual.schema_json == simplejson.loads(expected_resp.schema)
        self._assert_topic_values(actual.topic, expected_resp.topic)
        self._assert_note_values(actual.note, expected_resp.note)

    def _assert_schema_element_values(self, actual, expected_resp):
        assert len(actual) == len(expected_resp)
        attrs = ('note', 'schema_id', 'element_type', 'key', 'id')

        for i in range(0, len(actual)):
            self._assert_equal_multi_attrs(actual[i], expected_resp[i], *attrs)

    def _assert_avro_schemas_equal(self, actual, expected_resp):
        attrs = (
            'schema_id',
            'base_schema_id',
            'status',
            'primary_keys',
            'note',
            'created_at',
            'updated_at'
        )
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)
        assert actual.schema_json == expected_resp.schema_json
        self._assert_topic_values(actual.topic, expected_resp.topic)

    def _assert_note_values(self, actual, expected_resp):
        if actual is None or expected_resp is None:
            assert actual == expected_resp
            return
        attrs = (
            'reference_type',
            'note',
            'reference_id',
            'id',
            'created_at',
            'updated_at',
            'last_updated_by'
        )
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)

    def _assert_topic_values(self, actual, expected_resp):
        attrs = (
            'topic_id',
            'name',
            'contains_pii',
            'primary_keys',
            'created_at',
            'updated_at'
        )
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)
        self._assert_source_values(actual.source, expected_resp.source)

    def _assert_source_values(self, actual, expected_resp):
        attrs = ('source_id', 'name', 'owner_email', 'category')
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)
        assert actual.namespace.namespace_id == expected_resp.namespace.namespace_id

    def _assert_equal_multi_attrs(self, actual, expected, *attrs):
        for attr in attrs:
            assert getattr(actual, attr) == getattr(expected, attr)


class TestAPIClient(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name, containers):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name)

    def test_retry_api_call(self, schematizer, biz_schema):
        with mock.patch.object(
            schematizer,
            '_get_api_result',
            side_effect=[ConnectionError, ReadTimeout, None]
        ) as api_spy:
            schematizer._call_api(
                api=schematizer._client.schemas.get_schema_by_id,
                params={'schema_id': biz_schema.schema_id}
            )
            assert api_spy.call_count == 3


class TestGetSchemaById(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name):
        schema = self._register_avro_schema(yelp_namespace_name, biz_src_name)
        note = self._create_note(schema.schema_id, 'schema')
        schema.note = note
        return schema

    def test_get_non_cached_schema_by_id(self, schematizer, biz_schema):
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_by_id'
        ) as api_spy:
            actual = schematizer.get_schema_by_id(biz_schema.schema_id)
            self._assert_schema_values(actual, biz_schema)
            assert api_spy.call_count == 1

    def test_get_cached_schema_by_id(self, schematizer, biz_schema):
        schematizer.get_schema_by_id(biz_schema.schema_id)

        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_by_id'
        ) as schema_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as topic_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_schema_by_id(biz_schema.schema_id)
            self._assert_schema_values(actual, biz_schema)
            assert schema_api_spy.call_count == 0
            assert topic_api_spy.call_count == 0
            assert source_api_spy.call_count == 0


class TestGetSchemaElementsBySchemaId(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name)

    def test_get_schema_elements_by_schema_id(self, schematizer, biz_schema):
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_elements_by_schema_id'
        ) as api_spy:
            actual = schematizer.get_schema_elements_by_schema_id(
                biz_schema.schema_id
            )
            for element in actual:
                self._create_note(element.id, 'schema_element')
            actual = schematizer.get_schema_elements_by_schema_id(
                biz_schema.schema_id
            )
            for element in actual:
                assert element.note.note == self.note
                assert element.schema_id == biz_schema.schema_id
            assert api_spy.call_count == 2


class TestGetSchemasCreatedAfterDate(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def sorted_schemas(self, yelp_namespace_name, biz_src_name):
        biz_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name
        )

        time.sleep(1)
        schema_json = {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'simple'}]
        }
        simple_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_json=schema_json
        )

        time.sleep(1)
        schema_json = {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'baz'}]
        }
        baz_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_json=schema_json
        )

        return [biz_schema, simple_schema, baz_schema]

    def test_get_schemas_created_after_date_filter_by_min_id(
        self,
        sorted_schemas,
        schematizer
    ):
        created_at = sorted_schemas[0].created_at
        creation_timestamp = self._get_creation_timestamp(created_at)
        min_id = sorted_schemas[1].schema_id
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schemas_created_after'
        ) as schemas_api_spy:
            schemas = schematizer.get_schemas_created_after_date(
                created_after=creation_timestamp,
                min_id=min_id
            )
            # By default, Schematizer will fetch only 10 schemas at a time.
            assert schemas_api_spy.call_count == len(schemas) / 10 + 1
            for schema in schemas:
                assert schema.schema_id >= min_id

    def test_get_schemas_created_after_with_page_size(
        self,
        sorted_schemas,
        schematizer
    ):
        created_at = sorted_schemas[0].created_at
        creation_timestamp = self._get_creation_timestamp(created_at)

        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schemas_created_after'
        ) as schemas_api_spy:
            schemas = schematizer.get_schemas_created_after_date(
                created_after=creation_timestamp,
                min_id=1,
                page_size=1
            )
            # Since page size is 1, we would need to call api endpoint
            # len(schemas) + 1 times before we get a page with schemas less
            # than the page size.
            assert schemas_api_spy.call_count == len(schemas) + 1

    def test_get_schemas_created_after_date(self, schematizer):
        created_after = self._get_created_after()
        creation_timestamp = self._get_creation_timestamp(created_after)
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schemas_created_after'
        ) as api_spy:
            schemas = schematizer.get_schemas_created_after_date(
                creation_timestamp
            )
            # By default, Schematizer will fetch only 10 schemas at a time
            assert api_spy.call_count == len(schemas) / 10 + 1
            # Need to recreate created_after now that we may have tzinfo
            created_after = self._get_created_after(schemas[0].created_at)
            for schema in schemas:
                assert schema.created_at >= created_after

    def test_get_schemas_created_after_date_filter(self, schematizer):
        created_after = self._get_created_after()
        creation_timestamp = long(
            (created_after - datetime.fromtimestamp(0, created_after.tzinfo)).total_seconds()
        )
        day_two = (2016, 6, 10, 19, 10, 26, 0)
        created_after2 = datetime(*day_two, tzinfo=created_after.tzinfo)
        creation_timestamp2 = long(
            (created_after2 - datetime.fromtimestamp(0, created_after.tzinfo)).total_seconds()
        )
        schemas = schematizer.get_schemas_created_after_date(
            creation_timestamp
        )
        schemas_later = schematizer.get_schemas_created_after_date(
            creation_timestamp2
        )
        assert len(schemas) >= len(schemas_later)

    def test_get_schemas_created_after_date_cached(self, schematizer):
        created_after = self._get_created_after()
        creation_timestamp = self._get_creation_timestamp(created_after)
        schemas = schematizer.get_schemas_created_after_date(
            creation_timestamp)
        # Assert each element was cached properly
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_by_id'
        ) as schema_api_spy:
            for schema in schemas:
                actual = schematizer.get_schema_by_id(schema.schema_id)
                self._assert_avro_schemas_equal(actual, schema)
                assert schema_api_spy.call_count == 0


class TestGetSchmasByCriteria(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def sorted_schemas(self, yelp_namespace_name, biz_src_name):
        biz_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name
        )

        time.sleep(1)
        schema_json = {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'simple'}]
        }
        simple_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_json=schema_json
        )

        time.sleep(1)
        schema_json = {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'baz'}]
        }
        baz_schema = self._register_avro_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_json=schema_json
        )

        return [biz_schema, simple_schema, baz_schema]

    def test_get_schemas_by_created_date_and_id(
        self,
        sorted_schemas,
        schematizer
    ):
        created_after_date = self._get_creation_timestamp(
            sorted_schemas[0].created_at
        ) + 1
        schemas = schematizer.get_schemas_by_criteria(
            created_after=created_after_date,
            min_id=sorted_schemas[1].schema_id + 1,
        )
        assert len(schemas) == 1

    def test_get_schemas_by_count_and_id(self, sorted_schemas, schematizer):
        schemas = schematizer.get_schemas_by_criteria(
            min_id=sorted_schemas[0].schema_id + 1,
            count=1
        )
        assert len(schemas) == 1

    def test_get_schemas_by_criteria_cached(self, sorted_schemas, schematizer):
        schemas = schematizer.get_schemas_by_criteria(count=2)
        # Assert each element was cached properly
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_by_id'
        ) as schema_api_spy:
            for schema in schemas:
                actual = schematizer.get_schema_by_id(schema.schema_id)
                self._assert_avro_schemas_equal(actual, schema)
                assert schema_api_spy.call_count == 0


class TestGetSchemasByTopic(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name)

    def test_get_schemas_by_topic(self, schematizer, biz_schema):
        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'list_schemas_by_topic_name'
        ) as api_spy:
            topic_name = biz_schema.topic.name
            actual = schematizer.get_schemas_by_topic(topic_name)
            found_schema = False
            for schema in actual:
                # Find the schema in the list of schemas, and then check it's
                # values against our schema
                if schema.schema_id == biz_schema.schema_id:
                    found_schema = True
                    self._assert_schema_values(schema, biz_schema)
                    break
            assert found_schema
            assert api_spy.call_count == 1


class TestGetNamespaces(SchematizerClientTestBase):

    def test_get_namespaces(self, schematizer, biz_src_resp):
        actual = schematizer.get_namespaces()
        partial = Namespace(
            namespace_id=biz_src_resp.namespace.namespace_id,
            name=biz_src_resp.namespace.name
        )
        assert partial in actual


class TestGetSchemaBySchemaJson(SchematizerClientTestBase):

    @pytest.fixture
    def schema_json(self, yelp_namespace_name, biz_src_name):
        return {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'biz_id'}]
        }

    @pytest.fixture
    def schema_str(self, schema_json):
        return simplejson.dumps(schema_json)


class TestGetTopicByName(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_topic(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name).topic

    def test_get_non_cached_topic_by_name(self, schematizer, biz_topic):
        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as api_spy:
            actual = schematizer.get_topic_by_name(biz_topic.name)
            self._assert_topic_values(actual, biz_topic)
            assert api_spy.call_count == 1

    def test_get_cached_topic_by_name(self, schematizer, biz_topic):
        schematizer.get_topic_by_name(biz_topic.name)

        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as topic_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_topic_by_name(biz_topic.name)
            self._assert_topic_values(actual, biz_topic)
            assert topic_api_spy.call_count == 0
            assert source_api_spy.call_count == 0


class GetSourcesTestBase(SchematizerClientTestBase):

    @pytest.fixture(scope='class')
    def biz_src(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name
        ).topic.source

    @pytest.fixture(scope='class')
    def usr_src(self, yelp_namespace_name, usr_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            usr_src_name
        ).topic.source

    @pytest.fixture(scope='class')
    def cta_src(self, aux_namespace, cta_src_name):
        return self._register_avro_schema(
            aux_namespace,
            cta_src_name
        ).topic.source


class TestGetSources(GetSourcesTestBase):

    @pytest.fixture(scope='class')
    def sorted_sources(self, biz_src, usr_src, cta_src):
        return [biz_src, usr_src, cta_src]

    def test_get_all_sources(
        self,
        schematizer,
        sorted_sources
    ):
        actual = set(schematizer.get_sources())
        partial = {
            Source(
                source_id=source.source_id,
                name=source.name,
                owner_email=source.owner_email,
                namespace=Namespace(
                    source.namespace.namespace_id,
                    name=source.namespace.name
                ),
                category=source.category
            ) for source in sorted_sources
        }
        partial.issubset(actual)

    def test_get_sources_filter_by_min_id(
        self,
        sorted_sources,
        schematizer
    ):
        min_id = sorted_sources[1].source_id
        expected_sources = sorted_sources[1:]
        actual_sources = schematizer.get_sources(
            min_id=min_id
        )
        for actual_source, expected_source in zip(
            actual_sources,
            expected_sources
        ):
            self._assert_source_values(actual_source, expected_source)

    def test_get_sources_with_page_size(
        self,
        schematizer
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'list_sources'
        ) as sources_api_spy:
            actual_sources = schematizer.get_sources(
                min_id=1,
                page_size=1
            )
            # Since page size is 1, we would need to call api endpoint
            # len(sources) + 1 times before we get a page with sources less
            # than the page size.
            assert sources_api_spy.call_count == len(actual_sources) + 1


class TestGetSourceById(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_src(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name
        ).topic.source

    def test_get_non_cached_source_by_id(self, schematizer, biz_src):
        with self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as api_spy:
            actual = schematizer.get_source_by_id(biz_src.source_id)
            self._assert_source_values(actual, biz_src)
            assert api_spy.call_count == 1

    def test_get_cached_source_by_id(self, schematizer, biz_src):
        schematizer.get_source_by_id(biz_src.source_id)

        with self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_source_by_id(biz_src.source_id)
            self._assert_source_values(actual, biz_src)
            assert source_api_spy.call_count == 0


class TestGetSourcesByNamespace(GetSourcesTestBase):

    def test_get_sources_in_yelp_namespace_name(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src,
        usr_src
    ):
        actual = schematizer.get_sources_by_namespace(yelp_namespace_name)

        sorted_expected = sorted([biz_src, usr_src], key=lambda o: o.source_id)
        sorted_actual = sorted(actual, key=lambda o: o.source_id)
        for actual_src, expected_resp in zip(sorted_actual, sorted_expected):
            self._assert_source_values(actual_src, expected_resp)

    def test_get_sources_of_bad_namespace(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_sources_by_namespace('bad_namespace')

    def test_sources_should_be_cached(self, schematizer, yelp_namespace_name):
        sources = schematizer.get_sources_by_namespace(yelp_namespace_name)
        with self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_source_by_id(sources[0].source_id)
            assert actual == sources[0]
            assert source_api_spy.call_count == 0

    def test_get_sources_by_namespace_filter_by_min_id(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src,
        usr_src
    ):
        actual = schematizer.get_sources_by_namespace(
            yelp_namespace_name,
            min_id=biz_src.source_id + 1
        )
        expected = [usr_src]
        for actual_src, expected_resp in zip(actual, expected):
            self._assert_source_values(actual_src, expected_resp)

    def test_get_sources_by_namespace_filter_by_page_size(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src,
        usr_src
    ):
        actual = schematizer.get_sources_by_namespace(
            yelp_namespace_name,
            page_size=1
        )
        expected = [biz_src]
        for actual_src, expected_resp in zip(actual, expected):
            self._assert_source_values(actual_src, expected_resp)

    def test_get_sources_by_namespace_filter_by_page_size_and_min_id(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src,
        usr_src
    ):
        actual = schematizer.get_sources_by_namespace(
            yelp_namespace_name,
            page_size=2,
            min_id=biz_src.source_id + 1
        )
        expected = [usr_src]
        for actual_src, expected_resp in zip(actual, expected):
            self._assert_source_values(actual_src, expected_resp)


class TestGetTopicsBySourceId(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_topic(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name).topic

    @pytest.fixture(autouse=True, scope='class')
    def pii_biz_topic(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name,
            contains_pii=True
        ).topic

    def test_get_topics_of_biz_source(
        self,
        schematizer,
        biz_topic,
        pii_biz_topic
    ):
        actual = schematizer.get_topics_by_source_id(
            biz_topic.source.source_id
        )

        sorted_expected = sorted(
            [biz_topic, pii_biz_topic],
            key=lambda o: o.topic_id
        )
        sorted_actual = sorted(actual, key=lambda o: o.topic_id)
        for actual_topic, expected_resp in zip(sorted_actual, sorted_expected):
            self._assert_topic_values(actual_topic, expected_resp)

    def test_get_topics_of_bad_source_id(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_topics_by_source_id(0)

    def test_topics_should_be_cached(self, schematizer, biz_topic):
        topics = schematizer.get_topics_by_source_id(
            biz_topic.source.source_id
        )
        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as topic_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_topic_by_name(topics[0].name)
            assert actual == topics[0]
            assert topic_api_spy.call_count == 0
            assert source_api_spy.call_count == 0


class TestGetLatestTopicBySourceId(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_topic(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name
        ).topic

    def test_get_latest_topic_of_biz_source(self, schematizer, biz_topic):
        actual = schematizer.get_latest_topic_by_source_id(
            biz_topic.source.source_id
        )
        expected = biz_topic
        self._assert_topic_values(actual, expected)

    def test_get_latest_topic_of_bad_source(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_latest_topic_by_source_id(0)


class TestGetLatestSchemaByTopicName(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name)

    @pytest.fixture(autouse=True, scope='class')
    def biz_topic(self, biz_schema):
        return biz_schema.topic

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema_two(self, biz_schema):
        new_schema = simplejson.loads(biz_schema.schema)
        new_schema['fields'].append(
            {'type': 'int', 'doc': 'test', 'name': 'bar', 'default': 0}
        )
        return self._register_avro_schema(
            namespace=biz_schema.topic.source.namespace.name,
            source=biz_schema.topic.source.name,
            schema=simplejson.dumps(new_schema)
        )

    def test_get_latest_schema_of_biz_topic(
        self,
        schematizer,
        biz_topic,
        biz_schema_two
    ):
        actual = schematizer.get_latest_schema_by_topic_name(biz_topic.name)
        self._assert_schema_values(actual, biz_schema_two)

    def test_latest_schema_of_bad_topic(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_latest_schema_by_topic_name('bad_topic')

    def test_latest_schema_should_be_cached(self, schematizer, biz_topic):
        latest_schema = schematizer.get_latest_schema_by_topic_name(
            biz_topic.name
        )
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_schema_by_id'
        ) as schema_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as topic_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_schema_by_id(latest_schema.schema_id)
            assert actual == latest_schema
            assert schema_api_spy.call_count == 0
            assert topic_api_spy.call_count == 0
            assert source_api_spy.call_count == 0


class MetaAttrMappingTestBase(SchematizerClientTestBase):

    @pytest.fixture(scope='class')
    def user_schema(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name)

    @pytest.fixture
    def sample_schema(self, aux_namespace, biz_src_name):
        return self._register_avro_schema(aux_namespace, biz_src_name)

    @pytest.fixture
    def meta_attr_schema_id(self, registered_meta_attribute_schema):
        return registered_meta_attribute_schema.schema_id

    @pytest.fixture
    def user_namespace(self, user_schema):
        return user_schema.topic.source.namespace

    @pytest.fixture
    def user_source(self, user_schema):
        return user_schema.topic.source

    @pytest.fixture
    def sample_namespace(self, sample_schema):
        return sample_schema.topic.source.namespace

    @pytest.fixture
    def sample_source(self, sample_schema):
        return sample_schema.topic.source


class TestRegisterNamespaceMetaAttrMapping(MetaAttrMappingTestBase):

    def test_register_namespace_meta_attribute_mapping(
        self,
        schematizer,
        user_namespace,
        meta_attr_schema_id
    ):
        actual_meta_attr_mapping = schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        expected_meta_attr_mapping = MetaAttributeNamespaceMapping(
            namespace_id=user_namespace.namespace_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )
        assert actual_meta_attr_mapping == expected_meta_attr_mapping

    def test_register_same_namespace_meta_attribute_mapping_twice(
        self,
        schematizer,
        user_namespace,
        meta_attr_schema_id
    ):
        meta_attr_mapping_1 = schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        meta_attr_mapping_2 = schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        assert meta_attr_mapping_1 == meta_attr_mapping_2

    def test_registration_with_empty_namespace(self, schematizer, meta_attr_schema_id):
        with expect_HTTPError(400):
            schematizer.register_namespace_meta_attribute_mapping(
                namespace_name="",
                meta_attr_schema_id=meta_attr_schema_id
            )

    def test_registration_with_bad_namespace(
        self,
        schematizer,
        meta_attr_schema_id
    ):
        with expect_HTTPError(404):
            schematizer.register_namespace_meta_attribute_mapping(
                namespace_name="bad_namespace",
                meta_attr_schema_id=meta_attr_schema_id
            )


class TestDeleteNamespaceMetaAttrMapping(MetaAttrMappingTestBase):

    def test_delete_namespace_meta_attribute_mapping(
        self,
        schematizer,
        user_namespace,
        meta_attr_schema_id
    ):
        """ This test calls the delete api twice and verifies that the mapping
        gets deleted successfully first time and raises HTTPNotFound exception
        on calling the delete api again as the mapping has already been
        deleted.
        """
        schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        actual_meta_attr_mapping = schematizer.delete_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        expected_meta_attr_mapping = MetaAttributeNamespaceMapping(
            namespace_id=user_namespace.namespace_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )
        assert actual_meta_attr_mapping == expected_meta_attr_mapping
        with expect_HTTPError(404):
            schematizer.delete_namespace_meta_attribute_mapping(
                namespace_name=user_namespace.name,
                meta_attr_schema_id=meta_attr_schema_id
            )

    def test_delete_a_non_existent_namespace_meta_attribute_mapping(
        self,
        schematizer,
        user_namespace,
    ):
        with expect_HTTPError(404):
            schematizer.delete_namespace_meta_attribute_mapping(
                namespace_name=user_namespace.name,
                meta_attr_schema_id=0
            )

    def test_delete_meta_attr_mapping_with_invalid_namespace(
        self,
        schematizer,
        meta_attr_schema_id
    ):
        with expect_HTTPError(404):
            schematizer.delete_namespace_meta_attribute_mapping(
                namespace_name="invalid_namespace",
                meta_attr_schema_id=meta_attr_schema_id
            )

    def test_delete_meta_attr_mapping_with_empty_namespace(
        self,
        schematizer,
        meta_attr_schema_id
    ):
        with expect_HTTPError(400):
            schematizer.delete_namespace_meta_attribute_mapping(
                namespace_name="",
                meta_attr_schema_id=meta_attr_schema_id
            )


class TestGetMetaAttrMappingByNamespace(MetaAttrMappingTestBase):

    def test_get_namespace_meta_attribute_mappings(
        self,
        schematizer,
        user_namespace,
        meta_attr_schema_id
    ):
        schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        actual = schematizer.get_namespace_meta_attribute_mappings(
            namespace_name=user_namespace.name
        )
        expected_meta_attr_mapping = [MetaAttributeNamespaceMapping(
            namespace_id=user_namespace.namespace_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )]
        assert expected_meta_attr_mapping == actual

    def test_get_namespace_meta_attribute_mappings_when_none_exist(
        self,
        schematizer,
        sample_namespace
    ):
        meta_attr_mappings = schematizer.get_namespace_meta_attribute_mappings(
            namespace_name=sample_namespace.name
        )
        assert meta_attr_mappings == []

    def test_get_meta_attr_mapping_for_invalid_namespace(
        self,
        schematizer,
    ):
        with expect_HTTPError(404):
            schematizer.get_namespace_meta_attribute_mappings(
                namespace_name="bad_namespace"
            )


class TestRegisterSourceMetaAttrMapping(MetaAttrMappingTestBase):

    def test_register_source_meta_attribute_mapping(
        self,
        schematizer,
        user_source,
        meta_attr_schema_id
    ):
        actual_meta_attr_mapping = schematizer.register_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        expected_meta_attr_mapping = MetaAttributeSourceMapping(
            source_id=user_source.source_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )
        assert actual_meta_attr_mapping == expected_meta_attr_mapping

    def test_register_same_source_meta_attribute_mapping_twice(
        self,
        schematizer,
        user_source,
        meta_attr_schema_id
    ):
        meta_attr_mapping_1 = schematizer.register_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        meta_attr_mapping_2 = schematizer.register_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        assert meta_attr_mapping_1 == meta_attr_mapping_2

    def test_registration_with_invalid_source(self, schematizer, meta_attr_schema_id):
        with expect_HTTPError(404):
            schematizer.register_source_meta_attribute_mapping(
                source_id=0,
                meta_attr_schema_id=meta_attr_schema_id
            )


class TestDeleteSourceMetaAttrMapping(MetaAttrMappingTestBase):

    def test_delete_source_meta_attribute_mapping(
        self,
        schematizer,
        user_source,
        meta_attr_schema_id
    ):
        """ This test calls the delete api twice and verifies that the meta
        attribute mapping gets deleted successfully first time and raises
        HTTPNotFound exception on calling the delete api again as the mapping
        has already been deleted.
        """
        schematizer.register_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        actual_meta_attr_mapping = schematizer.delete_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        expected_meta_attr_mapping = MetaAttributeSourceMapping(
            source_id=user_source.source_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )
        assert actual_meta_attr_mapping == expected_meta_attr_mapping
        with expect_HTTPError(404):
            schematizer.delete_source_meta_attribute_mapping(
                source_id=user_source.source_id,
                meta_attr_schema_id=meta_attr_schema_id
            )

    def test_delete_a_non_existent_source_meta_attribute_mapping(
        self,
        schematizer,
        user_source,
    ):
        with expect_HTTPError(404):
            schematizer.delete_source_meta_attribute_mapping(
                source_id=user_source.source_id,
                meta_attr_schema_id=0
            )

    def test_delete_meta_attr_mapping_with_invalid_source(
        self,
        schematizer,
        meta_attr_schema_id
    ):
        with expect_HTTPError(404):
            schematizer.delete_source_meta_attribute_mapping(
                source_id=0,
                meta_attr_schema_id=meta_attr_schema_id
            )


class TestGetMetaAttrMappingBySource(MetaAttrMappingTestBase):

    def test_get_source_meta_attribute_mappings(
        self,
        schematizer,
        user_source,
        meta_attr_schema_id
    ):
        schematizer.register_source_meta_attribute_mapping(
            source_id=user_source.source_id,
            meta_attr_schema_id=meta_attr_schema_id
        )
        actual = schematizer.get_source_meta_attribute_mappings(user_source.source_id)
        expected_meta_attr_mapping = [MetaAttributeSourceMapping(
            source_id=user_source.source_id,
            meta_attribute_schema_id=meta_attr_schema_id
        )]
        assert expected_meta_attr_mapping == actual

    def test_get_source_meta_attribute_mappings_when_none_exist(
        self,
        schematizer,
        sample_source
    ):
        meta_attr_mappings = schematizer.get_source_meta_attribute_mappings(
            sample_source.source_id
        )
        assert meta_attr_mappings == []

    def test_get_meta_attr_mapping_for_invalid_source(
        self,
        schematizer,
    ):
        with expect_HTTPError(404):
            schematizer.get_source_meta_attribute_mappings(source_id=0)


class TestGetMetaAttrMappingBySchemaId(MetaAttrMappingTestBase):

    def test_get_meta_attributes_by_schema_id(
        self,
        schematizer,
        yelp_namespace_name,
        user_namespace,
        meta_attr_schema_id
    ):
        schematizer.register_namespace_meta_attribute_mapping(
            namespace_name=user_namespace.name,
            meta_attr_schema_id=meta_attr_schema_id
        )
        schema = self._register_avro_schema(yelp_namespace_name, "test_src")

        actual = schematizer.get_meta_attributes_by_schema_id(
            schema_id=schema.schema_id
        )
        expected_meta_attr_mapping = [meta_attr_schema_id]
        assert expected_meta_attr_mapping == actual

    def test_get_meta_attributes_by_schema_id_when_none_exist(
        self,
        schematizer,
        sample_schema
    ):
        meta_attr_ids = schematizer.get_meta_attributes_by_schema_id(
            schema_id=sample_schema.schema_id
        )
        assert meta_attr_ids == []

    def test_get_meta_attr_mapping_for_invalid_schema_id(
        self,
        schematizer,
    ):
        with expect_HTTPError(404):
            schematizer.get_source_meta_attribute_mappings(0)


class TestRegisterSchema(SchematizerClientTestBase):

    @pytest.fixture
    def schema_json(self, yelp_namespace_name, biz_src_name):
        return {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'biz_id'}]
        }

    @pytest.fixture
    def schema_str(self, schema_json):
        return simplejson.dumps(schema_json)

    def test_register_schema(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        actual = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)

    def test_register_schema_with_schema_json(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_json
    ):
        actual = schematizer.register_schema_from_schema_json(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_json=schema_json,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)

    def test_register_schema_with_base_schema(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        actual = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            base_schema_id=10
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)

    def test_register_same_schema_twice(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        schema_one = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        schema_two = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        assert schema_one == schema_two

    def test_register_same_schema_with_diff_base_schema(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        schema_one = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            base_schema_id=10
        )
        schema_two = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            base_schema_id=20
        )
        self._assert_two_schemas_have_diff_topics(schema_one, schema_two)
        assert schema_one.topic.source == schema_two.topic.source

    def test_register_same_schema_with_diff_pii(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        schema_one = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        schema_two = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=True
        )
        self._assert_two_schemas_have_diff_topics(schema_one, schema_two)
        assert not schema_one.topic.contains_pii
        assert schema_two.topic.contains_pii
        assert schema_one.topic.source == schema_two.topic.source

    def test_register_same_schema_with_diff_source(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str
    ):
        another_src = 'biz_user'
        schema_one = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        schema_two = schematizer.register_schema(
            namespace=yelp_namespace_name,
            source=another_src,
            schema_str=schema_str,
            source_owner_email=self.source_owner_email,
            contains_pii=False
        )
        self._assert_two_schemas_have_diff_topics(schema_one, schema_two)

        src_one = schema_one.topic.source
        src_two = schema_two.topic.source
        assert src_one.name == biz_src_name
        assert src_two.name == another_src
        assert src_one.source_id != src_two.source_id
        assert src_one.namespace == src_two.namespace

    def _assert_two_schemas_have_diff_topics(self, schema_one, schema_two):
        assert schema_one.schema_id != schema_two.schema_id
        assert schema_one.schema_json == schema_two.schema_json

        assert schema_one.topic.topic_id != schema_two.topic.topic_id
        assert schema_one.topic.name != schema_two.topic.name


class TestRegisterSchemaFromMySQL(SchematizerClientTestBase):

    @property
    def old_create_biz_table_stmt(self):
        return 'create table biz(id int(11) not null);'

    @property
    def alter_biz_table_stmt(self):
        return 'alter table biz add column name varchar(8);'

    @property
    def new_create_biz_table_stmt(self):
        return 'create table biz(id int(11) not null, name varchar(8));'

    @pytest.fixture
    def avro_schema_of_new_biz_table(self, biz_src_name):
        return {
            'type': 'record',
            'name': biz_src_name,
            'namespace': '',
            'doc': 'test',
            'fields': [
                {'name': 'id', 'doc': 'test', 'type': 'int'},
                {'name': 'name', 'doc': 'test',
                 'type': ['null', 'string'], 'maxlen': '8', 'default': None}
            ]
        }

    def test_register_for_new_table(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name
    ):
        actual = schematizer.register_schema_from_mysql_stmts(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            new_create_table_stmt=self.new_create_biz_table_stmt
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)

    def test_register_for_updated_existing_table(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name
    ):
        actual = schematizer.register_schema_from_mysql_stmts(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            new_create_table_stmt=self.new_create_biz_table_stmt,
            old_create_table_stmt=self.old_create_biz_table_stmt,
            alter_table_stmt=self.alter_biz_table_stmt
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)

    def test_register_same_schema_with_diff_pii(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name
    ):
        non_pii_schema = schematizer.register_schema_from_mysql_stmts(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            new_create_table_stmt=self.new_create_biz_table_stmt
        )
        pii_schema = schematizer.register_schema_from_mysql_stmts(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            source_owner_email=self.source_owner_email,
            contains_pii=True,
            new_create_table_stmt=self.new_create_biz_table_stmt
        )

        assert non_pii_schema.schema_id != pii_schema.schema_id
        assert non_pii_schema.schema_json == pii_schema.schema_json
        assert non_pii_schema.base_schema_id == pii_schema.base_schema_id

        assert non_pii_schema.topic.topic_id != pii_schema.topic.topic_id
        assert not non_pii_schema.topic.contains_pii
        assert pii_schema.topic.contains_pii

        assert non_pii_schema.topic.source == non_pii_schema.topic.source

    def test_register_schema_with_primary_keys(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name
    ):
        schema_sql = ('create table biz(id int(11) not null, name varchar(8), '
                      'primary key (id));')
        actual = schematizer.register_schema_from_mysql_stmts(
            namespace=yelp_namespace_name,
            source=biz_src_name,
            source_owner_email=self.source_owner_email,
            contains_pii=False,
            new_create_table_stmt=schema_sql
        )
        expected = self._get_schema_by_id(actual.schema_id)
        self._assert_schema_values(actual, expected)


class TestGetTopicsByCriteria(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def yelp_biz_topic(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(yelp_namespace_name, biz_src_name).topic

    @pytest.fixture(autouse=True, scope='class')
    def yelp_usr_topic(self, yelp_namespace_name, usr_src_name, yelp_biz_topic):
        # Because the minimum unit for created_at and updated_at timestamps
        # stored in the db table is 1 second, here it explicitly waits for 1
        # second to ensure these topics don't have the same created_at value.
        time.sleep(1)
        return self._register_avro_schema(yelp_namespace_name, usr_src_name).topic

    @pytest.fixture(autouse=True, scope='class')
    def aux_biz_topic(self, aux_namespace, biz_src_name, yelp_usr_topic):
        time.sleep(1)
        return self._register_avro_schema(aux_namespace, biz_src_name).topic

    def test_get_topics_in_one_namespace(
        self,
        schematizer,
        yelp_namespace_name,
        yelp_biz_topic,
        yelp_usr_topic
    ):
        actual = schematizer.get_topics_by_criteria(
            namespace_name=yelp_namespace_name
        )
        self._assert_topics_values(
            actual,
            expected_topics=[yelp_biz_topic, yelp_usr_topic]
        )

    def test_get_topics_of_one_source(
        self,
        schematizer,
        biz_src_name,
        yelp_biz_topic,
        aux_biz_topic
    ):
        actual = schematizer.get_topics_by_criteria(
            source_name=biz_src_name
        )
        self._assert_topics_values(
            actual,
            expected_topics=[yelp_biz_topic, aux_biz_topic]
        )

    def test_get_topics_of_bad_namesapce_name(self, schematizer):
        actual = schematizer.get_topics_by_criteria(namespace_name='foo')
        assert actual == []

    def test_get_topics_of_bad_source_name(self, schematizer):
        actual = schematizer.get_topics_by_criteria(source_name='foo')
        assert actual == []

    def test_get_topics_with_future_created_after_timestamp(self, schematizer):
        actual = schematizer.get_topics_by_criteria(
            created_after=int(time.time() + 60)
        )
        assert actual == []

    def test_topics_should_be_cached(self, schematizer, yelp_namespace_name):
        topics = schematizer.get_topics_by_criteria(
            namespace_name=yelp_namespace_name
        )
        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topic_by_topic_name'
        ) as topic_api_spy, self.attach_spy_on_api(
            schematizer._client,
            'sources',
            'get_source_by_id'
        ) as source_api_spy:
            actual = schematizer.get_topic_by_name(topics[0].name)
            assert actual == topics[0]
            assert topic_api_spy.call_count == 0
            assert source_api_spy.call_count == 0

    def test_get_topics_by_pagination(self, schematizer):
        # This test is based on current pagination setting in SchematizerClient,
        # which is set to default page size. This test mostly is the sanity
        # check for the pagination.
        namespace_name = self.get_new_name('dummy_namespace')
        source_name = self.get_new_name('dummy_source')
        expected_topics = []
        for i in range(schematizer.DEFAULT_PAGE_SIZE + 1):
            schema_json = {
                'type': 'enum',
                'name': 'dummy_enum_{}'.format(i),
                'symbols': ['a'],
                'doc': 'dummy schema'
            }
            topic = self._register_avro_schema(
                namespace=namespace_name,
                source=source_name,
                schema_json=schema_json
            ).topic
            expected_topics.append(topic)

        with self.attach_spy_on_api(
            schematizer._client,
            'topics',
            'get_topics_by_criteria'
        ) as topic_api_spy:
            actual = schematizer.get_topics_by_criteria(
                namespace_name=namespace_name,
                source_name=source_name
            )
            self._assert_topics_values(actual, expected_topics=expected_topics)
            # There are one more topic than the page size, therefore it'll need
            # 2 api calls to the service.
            assert topic_api_spy.call_count == 2

    def test_get_topics_with_id_greater_than_min_id(
        self,
        schematizer,
        yelp_namespace_name,
        yelp_biz_topic,
        yelp_usr_topic
    ):
        actual = schematizer.get_topics_by_criteria(
            namespace_name=yelp_namespace_name,
            min_id=yelp_biz_topic.topic_id + 1
        )
        self._assert_topics_values(
            actual,
            expected_topics=[yelp_usr_topic]
        )

    def test_get_only_one_topic(self, schematizer, yelp_namespace_name, yelp_biz_topic):
        actual = schematizer.get_topics_by_criteria(
            namespace_name=yelp_namespace_name,
            max_count=1
        )
        self._assert_topics_values(actual, expected_topics=[yelp_biz_topic])

    def _assert_topics_values(self, actual_topics, expected_topics):
        assert len(actual_topics) == len(expected_topics)
        for actual_topic, expected_resp in zip(actual_topics, expected_topics):
            self._assert_topic_values(actual_topic, expected_resp)


class TestIsAvroSchemaCompatible(SchematizerClientTestBase):

    @pytest.fixture(scope='class')
    def schema_json(self, yelp_namespace_name, biz_src_name):
        return {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [
                {'type': 'int', 'doc': 'test', 'name': 'biz_id'}
            ]
        }

    @pytest.fixture
    def schema_json_incompatible(self, yelp_namespace_name, biz_src_name):
        return {
            'type': 'record',
            'name': biz_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [
                {'type': 'int', 'doc': 'test', 'name': 'biz_id'},
                {'type': 'int', 'doc': 'test', 'name': 'new_field'}
            ]
        }

    @pytest.fixture(scope='class')
    def schema_str(self, schema_json):
        return simplejson.dumps(schema_json)

    @pytest.fixture
    def schema_str_incompatible(self, schema_json_incompatible):
        return simplejson.dumps(schema_json_incompatible)

    @pytest.fixture(autouse=True, scope='class')
    def biz_schema(self, yelp_namespace_name, biz_src_name, schema_str):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name,
            schema=schema_str
        )

    def test_is_avro_schema_compatible(
        self,
        schematizer,
        yelp_namespace_name,
        biz_src_name,
        schema_str,
        schema_str_incompatible
    ):
        assert schematizer.is_avro_schema_compatible(
            avro_schema_str=schema_str,
            namespace_name=yelp_namespace_name,
            source_name=biz_src_name
        )
        assert not schematizer.is_avro_schema_compatible(
            avro_schema_str=schema_str_incompatible,
            namespace_name=yelp_namespace_name,
            source_name=biz_src_name
        )


class TestFilterTopicsByPkeys(SchematizerClientTestBase):

    @pytest.fixture(autouse=True, scope='class')
    def pk_topic_resp(self, yelp_namespace_name, usr_src_name):
        pk_schema_json = {
            'type': 'record',
            'name': usr_src_name,
            'namespace': yelp_namespace_name,
            'doc': 'test',
            'fields': [
                {'type': 'int', 'doc': 'test', 'name': 'id', 'pkey': 1},
                {'type': 'int', 'doc': 'test', 'name': 'data'}
            ],
            'pkey': ['id']
        }
        return self._register_avro_schema(
            yelp_namespace_name,
            usr_src_name,
            schema=simplejson.dumps(pk_schema_json)
        ).topic

    def test_filter_topics_by_pkeys(
            self,
            schematizer,
            biz_topic_resp,
            pk_topic_resp
    ):
        topics = [
            biz_topic_resp.name,
            pk_topic_resp.name
        ]
        assert schematizer.filter_topics_by_pkeys(topics) == [pk_topic_resp.name]


class RegistrationTestBase(SchematizerClientTestBase):

    @pytest.fixture(scope="class")
    def dw_data_target_resp(self):
        return self._create_data_target()

    def _create_data_target(self):
        post_body = {
            'name': 'simple_name_{}'.format(random.random()),
            'target_type': 'redshift_{}'.format(random.random()),
            'destination': '{}.example.org'.format(random.random())
        }
        return self._get_client().data_targets.create_data_target(
            body=post_body
        ).result()

    @pytest.fixture(scope="class")
    def dw_con_group_resp(self, dw_data_target_resp):
        return self._create_consumer_group(dw_data_target_resp.data_target_id)

    def _create_consumer_group(self, data_target_id):
        return self._get_client().data_targets.create_consumer_group(
            data_target_id=data_target_id,
            body={'group_name': 'dw_{}'.format(random.random())}
        ).result()

    @pytest.fixture(scope="class")
    def dw_con_group_data_src_resp(self, dw_con_group_resp, biz_src_resp):
        return self._create_consumer_group_data_src(
            consumer_group_id=dw_con_group_resp.consumer_group_id,
            data_src_type='Source',
            data_src_id=biz_src_resp.source_id
        )

    def _create_consumer_group_data_src(
            self,
            consumer_group_id,
            data_src_type,
            data_src_id
    ):
        return self._get_client().consumer_groups.create_consumer_group_data_source(
            consumer_group_id=consumer_group_id,
            body={
                'data_source_type': data_src_type,
                'data_source_id': data_src_id
            }
        ).result()

    def _assert_data_target_values(self, actual, expected_resp):
        attrs = ('data_target_id', 'target_type', 'destination')
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)

    def _assert_consumer_group_values(self, actual, expected_resp):
        attrs = ('consumer_group_id', 'group_name')
        self._assert_equal_multi_attrs(actual, expected_resp, *attrs)
        self._assert_data_target_values(
            actual.data_target,
            expected_resp.data_target
        )


class TestCreateDataTarget(RegistrationTestBase):

    @property
    def random_name(self):
        return 'random_name'

    @property
    def random_target_type(self):
        return 'random_type'

    @property
    def random_destination(self):
        return 'random.destination'

    def test_create_data_target(self, schematizer):
        actual = schematizer.create_data_target(
            name=self.random_name,
            target_type=self.random_target_type,
            destination=self.random_destination
        )
        expected_resp = self._get_data_target_resp(actual.data_target_id)
        self._assert_data_target_values(actual, expected_resp)
        assert actual.target_type == self.random_target_type
        assert actual.destination == self.random_destination

    def test_invalid_empty_name(self, schematizer):
        with expect_HTTPError(400):
            schematizer.create_data_target(
                name='',
                target_type=self.random_target_type,
                destination=self.random_destination
            )

    def test_invalid_empty_target_type(self, schematizer):
        with expect_HTTPError(400):
            schematizer.create_data_target(
                name=self.random_name,
                target_type='',
                destination=self.random_destination
            )

    def test_invalid_empty_destination(self, schematizer):
        with expect_HTTPError(400):
            schematizer.create_data_target(
                name=self.random_name,
                target_type=self.random_target_type,
                destination=''
            )

    def _get_data_target_resp(self, data_target_id):
        return self._get_client().data_targets.get_data_target_by_id(
            data_target_id=data_target_id
        ).result()


class TestGetDataTargetById(RegistrationTestBase):

    def test_get_non_cached_data_target(
        self,
        schematizer,
        dw_data_target_resp
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'data_targets',
            'get_data_target_by_id'
        ) as api_spy:
            actual = schematizer.get_data_target_by_id(
                dw_data_target_resp.data_target_id
            )
            self._assert_data_target_values(actual, dw_data_target_resp)
            assert api_spy.call_count == 1

    def test_get_cached_data_target(self, schematizer, dw_data_target_resp):
        schematizer.get_data_target_by_id(dw_data_target_resp.data_target_id)

        with self.attach_spy_on_api(
            schematizer._client,
            'data_targets',
            'get_data_target_by_id'
        ) as data_target_api_spy:
            actual = schematizer.get_data_target_by_id(
                dw_data_target_resp.data_target_id
            )
            self._assert_data_target_values(actual, dw_data_target_resp)
            assert data_target_api_spy.call_count == 0

    def test_non_existing_data_target_id(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_data_target_by_id(data_target_id=0)


class TestGetDataTargetByName(RegistrationTestBase):

    def test_get_non_cached_data_target(
        self,
        schematizer,
        dw_data_target_resp
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'data_targets',
            'get_data_target_by_name'
        ) as api_spy:
            actual = schematizer.get_data_target_by_name(
                dw_data_target_resp.name
            )
            self._assert_data_target_values(actual, dw_data_target_resp)
            assert api_spy.call_count == 1

    def test_get_cached_data_target(self, schematizer, dw_data_target_resp):
        schematizer.get_data_target_by_name(dw_data_target_resp.name)

        with self.attach_spy_on_api(
            schematizer._client,
            'data_targets',
            'get_data_target_by_name'
        ) as data_target_api_spy:
            actual = schematizer.get_data_target_by_name(
                dw_data_target_resp.name
            )
            self._assert_data_target_values(actual, dw_data_target_resp)
            assert data_target_api_spy.call_count == 0

    def test_non_existing_data_target_name(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_data_target_by_name(
                data_target_name='bad test name'
            )


class TestCreateConsumerGroup(RegistrationTestBase):

    @pytest.fixture
    def random_group_name(self):
        return 'group_{}'.format(random.random())

    def test_create_consumer_group(
        self,
        schematizer,
        dw_data_target_resp,
        random_group_name
    ):
        actual = schematizer.create_consumer_group(
            group_name=random_group_name,
            data_target_id=dw_data_target_resp.data_target_id
        )
        expected_resp = self._get_consumer_group_resp(
            actual.consumer_group_id
        )
        self._assert_consumer_group_values(actual, expected_resp)
        assert actual.group_name == random_group_name

    def test_invalid_empty_group_name(self, schematizer, dw_data_target_resp):
        with expect_HTTPError(400):
            schematizer.create_consumer_group(
                group_name='',
                data_target_id=dw_data_target_resp.data_target_id
            )

    def test_duplicate_group_name(
        self,
        schematizer,
        dw_data_target_resp,
        random_group_name
    ):
        schematizer.create_consumer_group(
            group_name=random_group_name,
            data_target_id=dw_data_target_resp.data_target_id
        )
        with expect_HTTPError(400):
            schematizer.create_consumer_group(
                group_name=random_group_name,
                data_target_id=dw_data_target_resp.data_target_id
            )

    def test_non_existing_data_target(self, schematizer, random_group_name):
        with expect_HTTPError(404):
            schematizer.create_consumer_group(
                group_name=random_group_name,
                data_target_id=0
            )

    def _get_consumer_group_resp(self, consumer_group_id):
        return self._get_client().consumer_groups.get_consumer_group_by_id(
            consumer_group_id=consumer_group_id
        ).result()


class TestGetConsumerGroupById(RegistrationTestBase):

    def test_get_non_cached_consumer_group(
        self,
        schematizer,
        dw_con_group_resp
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'consumer_groups',
            'get_consumer_group_by_id'
        ) as api_spy:
            actual = schematizer.get_consumer_group_by_id(
                dw_con_group_resp.consumer_group_id
            )
            self._assert_consumer_group_values(actual, dw_con_group_resp)
            assert api_spy.call_count == 1

    def test_get_cached_consumer_group(self, schematizer, dw_con_group_resp):
        schematizer.get_consumer_group_by_id(
            dw_con_group_resp.consumer_group_id
        )

        with self.attach_spy_on_api(
            schematizer._client,
            'consumer_groups',
            'get_consumer_group_by_id'
        ) as consumer_group_api_spy:
            actual = schematizer.get_consumer_group_by_id(
                dw_con_group_resp.consumer_group_id
            )
            self._assert_consumer_group_values(actual, dw_con_group_resp)
            assert consumer_group_api_spy.call_count == 0

    def test_non_existing_consumer_group_id(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_consumer_group_by_id(consumer_group_id=0)


class TestCreateConsumerGroupDataSource(RegistrationTestBase):

    def test_create_consumer_group_data_source(
        self,
        schematizer,
        dw_con_group_resp,
        biz_src_resp
    ):
        actual = schematizer.create_consumer_group_data_source(
            consumer_group_id=dw_con_group_resp.consumer_group_id,
            data_source_type=DataSourceTypeEnum.Source,
            data_source_id=biz_src_resp.source_id
        )
        assert actual.consumer_group_id == dw_con_group_resp.consumer_group_id
        assert actual.data_source_type == DataSourceTypeEnum.Source
        assert actual.data_source_id == biz_src_resp.source_id

    def test_non_existing_consumer_group(self, schematizer, biz_src_resp):
        with expect_HTTPError(404):
            schematizer.create_consumer_group_data_source(
                consumer_group_id=0,
                data_source_type=DataSourceTypeEnum.Source,
                data_source_id=biz_src_resp.source_id
            )

    def test_non_existing_data_source(self, schematizer, dw_con_group_resp):
        with expect_HTTPError(404):
            schematizer.create_consumer_group_data_source(
                consumer_group_id=dw_con_group_resp.consumer_group_id,
                data_source_type=DataSourceTypeEnum.Source,
                data_source_id=0
            )


class TestGetTopicsByDataTargetId(RegistrationTestBase):

    def test_data_target_with_topics(
        self,
        schematizer,
        dw_data_target_resp,
        dw_con_group_data_src_resp,
        biz_src_resp,
        biz_topic_resp
    ):
        actual = schematizer.get_topics_by_data_target_id(
            dw_data_target_resp.data_target_id
        )
        for actual_topic, expected_resp in zip(actual, [biz_topic_resp]):
            self._assert_topic_values(actual_topic, expected_resp)

    def test_data_target_with_no_topic(self, schematizer):
        # no data source associated to the consumer group
        random_data_target = self._create_data_target()
        self._create_consumer_group(random_data_target.data_target_id)

        actual = schematizer.get_topics_by_data_target_id(
            random_data_target.data_target_id
        )
        assert actual == []

    def test_non_existing_data_target(self, schematizer):
        with expect_HTTPError(404):
            schematizer.get_topics_by_data_target_id(data_target_id=0)


class TestGetSchemaMigration(SchematizerClientTestBase):

    @pytest.fixture
    def new_schema(self):
        return {
            'type': 'record',
            'name': 'schema_a',
            'doc': 'test',
            'namespace': 'test_namespace',
            'fields': [{'type': 'int', 'doc': 'test', 'name': 'test_id'}]
        }

    @pytest.fixture(params=[True, False])
    def old_schema(self, request, new_schema):
        return new_schema if request.param else None

    def test_normal_schema_migration(
        self,
        schematizer,
        new_schema,
        old_schema
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'schema_migrations',
            'get_schema_migration'
        ) as api_spy:
            actual = schematizer.get_schema_migration(
                new_schema=new_schema,
                target_schema_type=TargetSchemaTypeEnum.redshift,
                old_schema=old_schema
            )
            assert isinstance(actual, list)
            assert len(actual) > 0
            assert api_spy.call_count == 1

    def test_invalid_schema(
        self,
        schematizer,
        new_schema
    ):
        with expect_HTTPError(422):
            schematizer._call_api(
                api=schematizer._client.schema_migrations.get_schema_migration,
                request_body={
                    'new_schema': '{}}',
                    'target_schema_type': TargetSchemaTypeEnum.redshift.name,
                }
            )

    def test_unsupported_schema_migration(
        self,
        schematizer,
        new_schema
    ):
        with expect_HTTPError(501):
            schematizer.get_schema_migration(
                new_schema=new_schema,
                target_schema_type=TargetSchemaTypeEnum.unsupported
            )


class TestGetDataTargetsBySchemaID(RegistrationTestBase):

    @pytest.fixture
    def biz_schema_id(self, yelp_namespace_name, biz_src_name):
        return self._register_avro_schema(
            yelp_namespace_name,
            biz_src_name
        ).schema_id

    def test_get_data_targets_with_scheam_id(
        self,
        schematizer,
        dw_con_group_data_src_resp,
        dw_data_target_resp,
        biz_schema_id
    ):
        with self.attach_spy_on_api(
            schematizer._client,
            'schemas',
            'get_data_targets_by_schema_id'
        ) as api_spy:
            actual = schematizer.get_data_targets_by_schema_id(
                biz_schema_id
            )
            self._assert_data_target_values(actual[0], dw_data_target_resp)
            assert api_spy.call_count == 1

    def test_get_data_targets_with_invalid_schema_id(
        self,
        schematizer,
    ):
        with expect_HTTPError(404):
            schematizer.get_data_targets_by_schema_id(-1)

    def test_data_targets_should_be_cached(
        self,
        schematizer,
        biz_schema_id,
        dw_data_target_resp
    ):
        data_targets = schematizer.get_data_targets_by_schema_id(
            biz_schema_id
        )
        with self.attach_spy_on_api(
            schematizer._client,
            'data_targets',
            'get_data_target_by_id'
        ) as schema_api_spy:
            actual = schematizer.get_data_target_by_id(
                dw_data_target_resp.data_target_id
            )
            self._assert_data_target_values(actual, data_targets[0])
            assert schema_api_spy.call_count == 0


@contextmanager
def expect_HTTPError(status_code):
    with pytest.raises(HTTPError) as exc_info:
        yield
    assert exc_info.value.response.status_code == status_code
