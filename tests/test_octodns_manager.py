#
#
#

from os import environ
from os.path import dirname, isfile, join
from unittest import TestCase
from unittest.mock import MagicMock, patch

from helpers import (
    DynamicProvider,
    GeoProvider,
    NoSshFpProvider,
    PlannableProvider,
    SimpleProvider,
    TemporaryDirectory,
)

from octodns import __VERSION__
from octodns.idna import IdnaDict, idna_encode
from octodns.manager import (
    MainThreadExecutor,
    Manager,
    ManagerException,
    _AggregateTarget,
)
from octodns.processor.base import BaseProcessor
from octodns.record import Create, Delete, Record
from octodns.yaml import safe_load
from octodns.zone import Zone

config_dir = join(dirname(__file__), 'config')


def get_config_filename(which):
    return join(config_dir, which)


class TestManager(TestCase):
    def test_missing_provider_class(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('missing-provider-class.yaml')).sync()
        self.assertTrue('missing class' in str(ctx.exception))

    def test_bad_provider_class(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('bad-provider-class.yaml')).sync()
        self.assertTrue('Unknown provider class' in str(ctx.exception))

    def test_bad_provider_class_module(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('bad-provider-class-module.yaml')
            ).sync()
        self.assertTrue('Unknown provider class' in str(ctx.exception))

    def test_bad_provider_class_no_module(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('bad-provider-class-no-module.yaml')
            ).sync()
        self.assertTrue('Unknown provider class' in str(ctx.exception))

    def test_missing_provider_config(self):
        # Missing provider config
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('missing-provider-config.yaml')).sync()
        self.assertTrue('provider config' in str(ctx.exception))

    def test_missing_env_config(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('missing-provider-env.yaml')).sync()
        self.assertTrue('missing env var' in str(ctx.exception))

    def test_missing_source(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('provider-problems.yaml')).sync(
                ['missing.sources.']
            )
        self.assertTrue('missing sources' in str(ctx.exception))

    def test_missing_targets(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('provider-problems.yaml')).sync(
                ['missing.targets.']
            )
        self.assertTrue('missing targets' in str(ctx.exception))

    def test_unknown_source(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('provider-problems.yaml')).sync(
                ['unknown.source.']
            )
        self.assertTrue('unknown source' in str(ctx.exception))

    def test_unknown_target(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('provider-problems.yaml')).sync(
                ['unknown.target.']
            )
        self.assertTrue('unknown target' in str(ctx.exception))

    def test_bad_plan_output_class(self):
        with self.assertRaises(ManagerException) as ctx:
            name = 'bad-plan-output-missing-class.yaml'
            Manager(get_config_filename(name)).sync()
        self.assertEqual('plan_output bad is missing class', str(ctx.exception))

    def test_bad_plan_output_config(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('bad-plan-output-config.yaml')).sync()
        self.assertEqual(
            'Incorrect plan_output config for bad', str(ctx.exception)
        )

    def test_source_only_as_a_target(self):
        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('provider-problems.yaml')).sync(
                ['not.targetable.']
            )
        self.assertTrue('does not support targeting' in str(ctx.exception))

    def test_always_dry_run(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            tc = Manager(get_config_filename('always-dry-run.yaml')).sync(
                dry_run=False
            )
            # only the stuff from subzone, unit.tests. is always-dry-run
            self.assertEqual(3, tc)

    def test_simple(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            tc = Manager(get_config_filename('simple.yaml')).sync(dry_run=False)
            self.assertEqual(28, tc)

            # try with just one of the zones
            tc = Manager(get_config_filename('simple.yaml')).sync(
                dry_run=False, eligible_zones=['unit.tests.']
            )
            self.assertEqual(22, tc)

            # the subzone, with 2 targets
            tc = Manager(get_config_filename('simple.yaml')).sync(
                dry_run=False, eligible_zones=['subzone.unit.tests.']
            )
            self.assertEqual(6, tc)

            # and finally the empty zone
            tc = Manager(get_config_filename('simple.yaml')).sync(
                dry_run=False, eligible_zones=['empty.']
            )
            self.assertEqual(0, tc)

            # Again with force
            tc = Manager(get_config_filename('simple.yaml')).sync(
                dry_run=False, force=True
            )
            self.assertEqual(28, tc)

            # Again with max_workers = 1
            tc = Manager(
                get_config_filename('simple.yaml'), max_workers=1
            ).sync(dry_run=False, force=True)
            self.assertEqual(28, tc)

            # Include meta
            tc = Manager(
                get_config_filename('simple.yaml'),
                max_workers=1,
                include_meta=True,
            ).sync(dry_run=False, force=True)
            self.assertEqual(33, tc)

    def test_idna_eligible_zones(self):
        # loading w/simple, but we'll be blowing it away and doing some manual
        # stuff
        manager = Manager(get_config_filename('simple.yaml'))

        # these configs won't be valid, but that's fine we can test what we're
        # after based on exceptions raised
        manager.config['zones'] = manager._config_zones(
            {'déjà.vu.': {}, 'deja.vu.': {}, idna_encode('こんにちは.jp.'): {}}
        )

        # refer to them with utf-8
        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=('déjà.vu.',))
        self.assertEqual('Zone déjà.vu. is missing sources', str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=('deja.vu.',))
        self.assertEqual('Zone deja.vu. is missing sources', str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=('こんにちは.jp.',))
        self.assertEqual(
            'Zone こんにちは.jp. is missing sources', str(ctx.exception)
        )

        # refer to them with idna (exceptions are still utf-8
        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=(idna_encode('déjà.vu.'),))
        self.assertEqual('Zone déjà.vu. is missing sources', str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=(idna_encode('deja.vu.'),))
        self.assertEqual('Zone deja.vu. is missing sources', str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            manager.sync(eligible_zones=(idna_encode('こんにちは.jp.'),))
        self.assertEqual(
            'Zone こんにちは.jp. is missing sources', str(ctx.exception)
        )

    def test_eligible_sources(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            # Only allow a target that doesn't exist
            tc = Manager(get_config_filename('simple.yaml')).sync(
                eligible_sources=['foo']
            )
            self.assertEqual(0, tc)

    def test_eligible_targets(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            # Only allow a target that doesn't exist
            tc = Manager(get_config_filename('simple.yaml')).sync(
                eligible_targets=['foo']
            )
            self.assertEqual(0, tc)

    def test_aliases(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            # Alias zones with a valid target.
            tc = Manager(get_config_filename('simple-alias-zone.yaml')).sync()
            self.assertEqual(0, tc)

            # Alias zone with an invalid target.
            with self.assertRaises(ManagerException) as ctx:
                tc = Manager(
                    get_config_filename('unknown-source-zone.yaml')
                ).sync()
            self.assertEqual(
                'Invalid alias zone alias.tests.: source zone '
                'does-not-exists.tests. does not exist',
                str(ctx.exception),
            )

            # Alias zone that points to another alias zone.
            with self.assertRaises(ManagerException) as ctx:
                tc = Manager(get_config_filename('alias-zone-loop.yaml')).sync()
            self.assertEqual(
                'Invalid alias zone alias-loop.tests.: source '
                'zone alias.tests. is an alias zone',
                str(ctx.exception),
            )

            # Sync an alias without the zone it refers to
            with self.assertRaises(ManagerException) as ctx:
                tc = Manager(
                    get_config_filename('simple-alias-zone.yaml')
                ).sync(eligible_zones=["alias.tests."])
            self.assertEqual(
                'Zone alias.tests. cannot be synced without zone '
                'unit.tests. sinced it is aliased',
                str(ctx.exception),
            )

    def test_compare(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            manager = Manager(get_config_filename('simple.yaml'))

            # make sure this was pulled in from the config
            self.assertEqual(2, manager._executor._max_workers)

            changes = manager.compare(['in'], ['in'], 'unit.tests.')
            self.assertEqual([], changes)

            # Create an empty unit.test zone config
            with open(join(tmpdir.dirname, 'unit.tests.yaml'), 'w') as fh:
                fh.write('---\n{}')

            # compare doesn't use _process_desired_zone and thus doesn't filter
            # out root NS records, that seems fine/desirable
            changes = manager.compare(['in'], ['dump'], 'unit.tests.')
            self.assertEqual(23, len(changes))

            # Compound sources with varying support
            changes = manager.compare(
                ['in', 'nosshfp'], ['dump'], 'unit.tests.'
            )
            self.assertEqual(22, len(changes))

            with self.assertRaises(ManagerException) as ctx:
                manager.compare(['nope'], ['dump'], 'unit.tests.')
            self.assertEqual('Unknown source: nope', str(ctx.exception))

    def test_aggregate_target(self):
        simple = SimpleProvider()
        geo = GeoProvider()
        dynamic = DynamicProvider()
        nosshfp = NoSshFpProvider()

        targets = [simple, geo]
        at = _AggregateTarget(targets)
        # expected targets
        self.assertEqual(targets, at.targets)
        # union of their SUPPORTS
        self.assertEqual(set(('A')), at.SUPPORTS)

        # unknown property will go up into super and throw the normal
        # exception
        with self.assertRaises(AttributeError) as ctx:
            at.FOO
        self.assertEqual(
            '_AggregateTarget object has no attribute FOO', str(ctx.exception)
        )

        self.assertFalse(_AggregateTarget([simple, simple]).SUPPORTS_GEO)
        self.assertFalse(_AggregateTarget([simple, geo]).SUPPORTS_GEO)
        self.assertFalse(_AggregateTarget([geo, simple]).SUPPORTS_GEO)
        self.assertTrue(_AggregateTarget([geo, geo]).SUPPORTS_GEO)

        self.assertFalse(_AggregateTarget([simple, simple]).SUPPORTS_DYNAMIC)
        self.assertFalse(_AggregateTarget([simple, dynamic]).SUPPORTS_DYNAMIC)
        self.assertFalse(_AggregateTarget([dynamic, simple]).SUPPORTS_DYNAMIC)
        self.assertTrue(_AggregateTarget([dynamic, dynamic]).SUPPORTS_DYNAMIC)

        zone = Zone('unit.tests.', [])
        record = Record.new(
            zone,
            'sshfp',
            {
                'ttl': 60,
                'type': 'SSHFP',
                'value': {
                    'algorithm': 1,
                    'fingerprint_type': 1,
                    'fingerprint': 'abcdefg',
                },
            },
        )
        self.assertTrue(simple.supports(record))
        self.assertFalse(nosshfp.supports(record))
        self.assertTrue(_AggregateTarget([simple, simple]).supports(record))
        self.assertFalse(_AggregateTarget([simple, nosshfp]).supports(record))

    def test_dump(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            manager = Manager(get_config_filename('simple.yaml'))

            with self.assertRaises(ManagerException) as ctx:
                manager.dump(
                    zone='unit.tests.',
                    output_dir=tmpdir.dirname,
                    split=True,
                    sources=['nope'],
                )
            self.assertEqual('Unknown source: nope', str(ctx.exception))

            manager.dump(
                zone='unit.tests.',
                output_dir=tmpdir.dirname,
                split=True,
                sources=['in'],
            )

            # make sure this fails with an ManagerException and not a KeyError
            # when trying to find sub zones
            with self.assertRaises(ManagerException):
                manager.dump(
                    zone='unknown.zone.',
                    output_dir=tmpdir.dirname,
                    split=True,
                    sources=['in'],
                )

    def test_dump_empty(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            manager = Manager(get_config_filename('simple.yaml'))

            manager.dump(
                zone='empty.', output_dir=tmpdir.dirname, sources=['in']
            )

            with open(join(tmpdir.dirname, 'empty.yaml')) as fh:
                data = safe_load(fh, False)
                self.assertFalse(data)

    def test_dump_output_provider(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            # this time we'll use seperate tmp dirs
            with TemporaryDirectory() as tmpdir2:
                environ['YAML_TMP_DIR2'] = tmpdir2.dirname
                manager = Manager(get_config_filename('simple.yaml'))

                # we're going to tell it to use dump2 to do the dumping, but a
                # copy should be made and directory set to tmpdir.dirname
                # rather than 2's tmpdir2.dirname
                manager.dump(
                    zone='unit.tests.',
                    output_dir=tmpdir.dirname,
                    output_provider='dump2',
                    sources=['in'],
                )

                self.assertTrue(isfile(join(tmpdir.dirname, 'unit.tests.yaml')))
                self.assertFalse(
                    isfile(join(tmpdir2.dirname, 'unit.tests.yaml'))
                )

                # let's run that again, this time telling it to use tmpdir2 and
                # dump2 which should allow it to skip the copying
                manager.dump(
                    zone='unit.tests.',
                    output_dir=tmpdir2.dirname,
                    output_provider='dump2',
                    sources=['in'],
                )
                self.assertTrue(
                    isfile(join(tmpdir2.dirname, 'unit.tests.yaml'))
                )

                # tell it to use an output_provider that doesn't exist
                with self.assertRaises(ManagerException) as ctx:
                    manager.dump(
                        zone='unit.tests.',
                        output_dir=tmpdir.dirname,
                        output_provider='nope',
                        sources=['in'],
                    )
                self.assertEqual(
                    'Unknown output_provider: nope', str(ctx.exception)
                )

                # tell it to use an output_provider that doesn't support
                # directory
                with self.assertRaises(ManagerException) as ctx:
                    manager.dump(
                        zone='unit.tests.',
                        output_dir=tmpdir.dirname,
                        output_provider='simple',
                        sources=['in'],
                    )
                self.assertEqual(
                    'output_provider=simple, does not support '
                    'directory property',
                    str(ctx.exception),
                )

                # hack a directory property onto the simple provider so that
                # it'll pass that check and fail the copy one instead
                manager.providers['simple'].directory = 42
                with self.assertRaises(ManagerException) as ctx:
                    manager.dump(
                        zone='unit.tests.',
                        output_dir=tmpdir.dirname,
                        output_provider='simple',
                        sources=['in'],
                    )
                self.assertEqual(
                    'output_provider=simple, does not support copy method',
                    str(ctx.exception),
                )

    def test_dump_split(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            manager = Manager(get_config_filename('simple-split.yaml'))

            with self.assertRaises(ManagerException) as ctx:
                manager.dump(
                    zone='unit.tests.',
                    output_dir=tmpdir.dirname,
                    split=True,
                    sources=['nope'],
                )
            self.assertEqual('Unknown source: nope', str(ctx.exception))

            manager.dump(
                zone='unit.tests.',
                output_dir=tmpdir.dirname,
                split=True,
                sources=['in'],
            )

            # make sure this fails with an ManagerException and not a KeyError
            # when trying to find sub zones
            with self.assertRaises(ManagerException):
                manager.dump(
                    zone='unknown.zone.',
                    output_dir=tmpdir.dirname,
                    split=True,
                    sources=['in'],
                )

    def test_validate_configs(self):
        Manager(get_config_filename('simple-validate.yaml')).validate_configs()

        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('missing-sources.yaml')
            ).validate_configs()
        self.assertTrue('missing sources' in str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('unknown-provider.yaml')
            ).validate_configs()
        self.assertTrue('unknown source' in str(ctx.exception))

        # Alias zone using an invalid source zone.
        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('unknown-source-zone.yaml')
            ).validate_configs()
        self.assertTrue('does not exist' in str(ctx.exception))

        # Alias zone that points to another alias zone.
        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('alias-zone-loop.yaml')
            ).validate_configs()
        self.assertTrue('is an alias zone' in str(ctx.exception))

        # Valid config file using an alias zone.
        Manager(
            get_config_filename('simple-alias-zone.yaml')
        ).validate_configs()

        with self.assertRaises(ManagerException) as ctx:
            Manager(
                get_config_filename('unknown-processor.yaml')
            ).validate_configs()
        self.assertTrue('unknown processor' in str(ctx.exception))

    def test_get_zone(self):
        Manager(get_config_filename('simple.yaml')).get_zone('unit.tests.')

        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('simple.yaml')).get_zone('unit.tests')
        self.assertTrue('missing ending dot' in str(ctx.exception))

        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('simple.yaml')).get_zone(
                'unknown-zone.tests.'
            )
        self.assertTrue('Unknown zone name' in str(ctx.exception))

    def test_populate_lenient_fallback(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            # Only allow a target that doesn't exist
            manager = Manager(get_config_filename('simple.yaml'))

            class NoLenient(SimpleProvider):
                def populate(self, zone):
                    pass

            # This should be ok, we'll fall back to not passing it
            manager._populate_and_plan('unit.tests.', [], [NoLenient()], [])

            class OtherType(SimpleProvider):
                def populate(self, zone, lenient=False):
                    raise TypeError('something else')

            # This will blow up, we don't fallback for source
            with self.assertRaises(TypeError) as ctx:
                manager._populate_and_plan('unit.tests.', [], [OtherType()], [])
            self.assertEqual('something else', str(ctx.exception))

    def test_plan_processors_fallback(self):
        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname
            environ['YAML_TMP_DIR2'] = tmpdir.dirname
            # Only allow a target that doesn't exist
            manager = Manager(get_config_filename('simple.yaml'))

            class NoProcessors(SimpleProvider):
                def plan(self, zone):
                    pass

            # This should be ok, we'll fall back to not passing it
            manager._populate_and_plan('unit.tests.', [], [], [NoProcessors()])

            class OtherType(SimpleProvider):
                def plan(self, zone, processors):
                    raise TypeError('something else')

            # This will blow up, we don't fallback for source
            with self.assertRaises(TypeError) as ctx:
                manager._populate_and_plan('unit.tests.', [], [], [OtherType()])
            self.assertEqual('something else', str(ctx.exception))

    @patch('octodns.manager.Manager._get_named_class')
    def test_sync_passes_file_handle(self, mock):
        plan_output_mock = MagicMock()
        plan_output_class_mock = MagicMock()
        plan_output_class_mock.return_value = plan_output_mock
        mock.return_value = (plan_output_class_mock, 'ignored', 'ignored')
        fh_mock = MagicMock()

        Manager(get_config_filename('plan-output-filehandle.yaml')).sync(
            plan_output_fh=fh_mock
        )

        # Since we only care about the fh kwarg, and different _PlanOutputs are
        # are free to require arbitrary kwargs anyway, we concern ourselves
        # with checking the value of fh only.
        plan_output_mock.run.assert_called()
        _, kwargs = plan_output_mock.run.call_args
        self.assertEqual(fh_mock, kwargs.get('fh'))

    def test_processor_config(self):
        # Smoke test loading a valid config
        manager = Manager(get_config_filename('processors.yaml'))
        self.assertEqual(
            ['noop', 'test', 'global-counter'], list(manager.processors.keys())
        )
        # make sure we got the global processor and that it's count is 0 now
        self.assertEqual(['global-counter'], manager.global_processors)
        self.assertEqual(0, manager.processors['global-counter'].count)
        # This zone specifies a valid processor
        manager.sync(['unit.tests.'])
        # make sure the global processor ran and counted some records
        self.assertTrue(manager.processors['global-counter'].count >= 25)

        with self.assertRaises(ManagerException) as ctx:
            # This zone specifies a non-existent processor
            manager.sync(['bad.unit.tests.'])
        self.assertTrue(
            'Zone bad.unit.tests., unknown processor: '
            'doesnt-exist' in str(ctx.exception)
        )

        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('processors-missing-class.yaml'))
        self.assertTrue(
            'Processor no-class is missing class' in str(ctx.exception)
        )

        with self.assertRaises(ManagerException) as ctx:
            Manager(get_config_filename('processors-wants-config.yaml'))
        self.assertTrue(
            'Incorrect processor config for wants-config' in str(ctx.exception)
        )

    def test_processors(self):
        manager = Manager(get_config_filename('simple.yaml'))

        targets = [PlannableProvider('prov')]

        zone = Zone('unit.tests.', [])
        record = Record.new(
            zone, 'a', {'ttl': 30, 'type': 'A', 'value': '1.2.3.4'}
        )

        # muck with sources
        class MockProcessor(BaseProcessor):
            def process_source_zone(self, zone, sources):
                zone = zone.copy()
                zone.add_record(record)
                return zone

        mock = MockProcessor('mock')
        plans, zone = manager._populate_and_plan(
            'unit.tests.', [mock], [], targets
        )
        # Our mock was called and added the record
        self.assertEqual(record, list(zone.records)[0])
        # We got a create for the thing added to the expected state (source)
        self.assertIsInstance(plans[0][1].changes[0], Create)

        # muck with targets
        class MockProcessor(BaseProcessor):
            def process_target_zone(self, zone, target):
                zone = zone.copy()
                zone.add_record(record)
                return zone

        mock = MockProcessor('mock')
        plans, zone = manager._populate_and_plan(
            'unit.tests.', [mock], [], targets
        )
        # No record added since it's target this time
        self.assertFalse(zone.records)
        # We got a delete for the thing added to the existing state (target)
        self.assertIsInstance(plans[0][1].changes[0], Delete)

        # muck with plans
        class MockProcessor(BaseProcessor):
            def process_target_zone(self, zone, target):
                zone = zone.copy()
                zone.add_record(record)
                return zone

            def process_plan(self, plans, sources, target):
                # get rid of the change
                plans.changes.pop(0)

        mock = MockProcessor('mock')
        plans, zone = manager._populate_and_plan(
            'unit.tests.', [mock], [], targets
        )
        # We planned a delete again, but this time removed it from the plan, so
        # no plans
        self.assertFalse(plans)

    def test_try_version(self):
        manager = Manager(get_config_filename('simple.yaml'))

        class DummyModule(object):
            __VERSION__ = '2.3.4'

        dummy_module = DummyModule()

        # use importlib.metadata.version
        self.assertTrue(
            __VERSION__,
            manager._try_version(
                'octodns', module=dummy_module, version='1.2.3'
            ),
        )

        # use module
        self.assertTrue(
            manager._try_version('doesnt-exist', module=dummy_module)
        )

        # fall back to version, preferred over module
        self.assertEqual(
            '1.2.3',
            manager._try_version(
                'doesnt-exist', module=dummy_module, version='1.2.3'
            ),
        )

    def test_subzone_handling(self):
        manager = Manager(get_config_filename('simple.yaml'))

        # tree with multiple branches, one that skips
        manager.config['zones'] = {
            'unit.tests.': {},
            'sub.unit.tests.': {},
            'another.sub.unit.tests.': {},
            'skipped.alevel.unit.tests.': {},
        }

        self.assertEqual(
            {'another.sub', 'sub', 'skipped.alevel'},
            manager.configured_sub_zones('unit.tests.'),
        )
        self.assertEqual(
            {'another'}, manager.configured_sub_zones('sub.unit.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('another.sub.unit.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('skipped.alevel.unit.tests.')
        )

        # unknown zone names return empty set
        self.assertEqual(set(), manager.configured_sub_zones('unknown.tests.'))

        # two parallel trees, make sure they don't interfere
        manager.config['zones'] = {
            'unit.tests.': {},
            'unit2.tests.': {},
            'sub.unit.tests.': {},
            'sub.unit2.tests.': {},
            'another.sub.unit.tests.': {},
            'another.sub.unit2.tests.': {},
            'skipped.alevel.unit.tests.': {},
            'skipped.alevel.unit2.tests.': {},
        }
        manager._configured_sub_zones = None
        self.assertEqual(
            {'another.sub', 'sub', 'skipped.alevel'},
            manager.configured_sub_zones('unit.tests.'),
        )
        self.assertEqual(
            {'another'}, manager.configured_sub_zones('sub.unit.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('another.sub.unit.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('skipped.alevel.unit.tests.')
        )
        self.assertEqual(
            {'another.sub', 'sub', 'skipped.alevel'},
            manager.configured_sub_zones('unit2.tests.'),
        )
        self.assertEqual(
            {'another'}, manager.configured_sub_zones('sub.unit2.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('another.sub.unit2.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('skipped.alevel.unit2.tests.')
        )

        # zones that end with names of others
        manager.config['zones'] = {
            'unit.tests.': {},
            'uunit.tests.': {},
            'uuunit.tests.': {},
        }
        manager._configured_sub_zones = None
        self.assertEqual(set(), manager.configured_sub_zones('unit.tests.'))
        self.assertEqual(set(), manager.configured_sub_zones('uunit.tests.'))
        self.assertEqual(set(), manager.configured_sub_zones('uuunit.tests.'))

        # skipping multiple levels
        manager.config['zones'] = {
            'unit.tests.': {},
            'foo.bar.baz.unit.tests.': {},
        }
        manager._configured_sub_zones = None
        self.assertEqual(
            {'foo.bar.baz'}, manager.configured_sub_zones('unit.tests.')
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('foo.bar.baz.unit.tests.')
        )

        # different TLDs
        manager.config['zones'] = {
            'unit.tests.': {},
            'foo.unit.tests.': {},
            'unit.org.': {},
            'bar.unit.org.': {},
        }
        manager._configured_sub_zones = None
        self.assertEqual({'foo'}, manager.configured_sub_zones('unit.tests.'))
        self.assertEqual(set(), manager.configured_sub_zones('foo.unit.tests.'))
        self.assertEqual({'bar'}, manager.configured_sub_zones('unit.org.'))
        self.assertEqual(set(), manager.configured_sub_zones('bar.unit.org.'))

        # starting a beyond 2 levels
        manager.config['zones'] = {
            'foo.unit.tests.': {},
            'bar.foo.unit.tests.': {},
            'bleep.bloop.foo.unit.tests.': {},
        }
        manager._configured_sub_zones = None
        self.assertEqual(
            {'bar', 'bleep.bloop'},
            manager.configured_sub_zones('foo.unit.tests.'),
        )
        self.assertEqual(
            set(), manager.configured_sub_zones('bar.foo.unit.tests.')
        )

    def test_config_zones(self):
        manager = Manager(get_config_filename('simple.yaml'))

        # empty == empty
        self.assertEqual({}, manager._config_zones({}))

        # single ascii comes back as-is, but in a IdnaDict
        zones = manager._config_zones({'unit.tests.': 42})
        self.assertEqual({'unit.tests.': 42}, zones)
        self.assertIsInstance(zones, IdnaDict)

        # single utf-8 comes back idna encoded
        self.assertEqual(
            {idna_encode('Déjà.vu.'): 42},
            dict(manager._config_zones({'Déjà.vu.': 42})),
        )

        # ascii and non-matching idna as ok
        self.assertEqual(
            {idna_encode('déjà.vu.'): 42, 'deja.vu.': 43},
            dict(
                manager._config_zones(
                    {idna_encode('déjà.vu.'): 42, 'deja.vu.': 43}
                )
            ),
        )

        with self.assertRaises(ManagerException) as ctx:
            # zone configured with both utf-8 and idna is an error
            manager._config_zones({'Déjà.vu.': 42, idna_encode('Déjà.vu.'): 43})
        self.assertEqual(
            '"déjà.vu." configured both in utf-8 and idna "xn--dj-kia8a.vu."',
            str(ctx.exception),
        )

    def test_auto_arpa(self):
        manager = Manager(get_config_filename('simple-arpa.yaml'))

        with TemporaryDirectory() as tmpdir:
            environ['YAML_TMP_DIR'] = tmpdir.dirname

            # we can sync eligible_zones so long as they're not arpa
            tc = manager.sync(dry_run=False, eligible_zones=['unit.tests.'])
            self.assertEqual(22, tc)
            # can't do partial syncs that include arpa zones
            with self.assertRaises(ManagerException) as ctx:
                manager.sync(
                    dry_run=False,
                    eligible_zones=['unit.tests.', '3.2.2.in-addr.arpa.'],
                )
            self.assertEqual(
                'ARPA zones cannot be synced during partial runs when auto_arpa is enabled',
                str(ctx.exception),
            )

            # same for eligible_sources
            tc = manager.sync(
                dry_run=False,
                eligible_zones=['unit.tests.'],
                eligible_sources=['in'],
            )
            self.assertEqual(22, tc)
            # can't do partial syncs that include arpa zones
            with self.assertRaises(ManagerException) as ctx:
                manager.sync(dry_run=False, eligible_sources=['in'])
            self.assertEqual(
                'eligible_sources is incompatible with auto_arpa',
                str(ctx.exception),
            )

            # same for eligible_targets
            tc = manager.sync(
                dry_run=False,
                eligible_zones=['unit.tests.'],
                eligible_targets=['dump'],
            )
            self.assertEqual(22, tc)
            # can't do partial syncs that include arpa zones
            with self.assertRaises(ManagerException) as ctx:
                manager.sync(dry_run=False, eligible_targets=['dump'])
            self.assertEqual(
                'eligible_targets is incompatible with auto_arpa',
                str(ctx.exception),
            )

            # full sync with arpa is fine, 2 extra records from it
            tc = manager.sync(dry_run=False)
            self.assertEqual(26, tc)


class TestMainThreadExecutor(TestCase):
    def test_success(self):
        mte = MainThreadExecutor()

        future = mte.submit(self.success, 42)
        self.assertEqual(42, future.result())

        future = mte.submit(self.success, ret=43)
        self.assertEqual(43, future.result())

    def test_exception(self):
        mte = MainThreadExecutor()

        e = Exception('boom')
        future = mte.submit(self.exception, e)
        with self.assertRaises(Exception) as ctx:
            future.result()
        self.assertEqual(e, ctx.exception)

        future = mte.submit(self.exception, e=e)
        with self.assertRaises(Exception) as ctx:
            future.result()
        self.assertEqual(e, ctx.exception)

    def success(self, ret):
        return ret

    def exception(self, e):
        raise e
