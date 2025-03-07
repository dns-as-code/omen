#
#
#

from collections import defaultdict
from logging import getLogger

from ..equality import EqualityTupleMixin
from ..idna import IdnaError, idna_decode, idna_encode
from .change import Update
from .exception import RecordException, ValidationError


class Record(EqualityTupleMixin):
    log = getLogger('Record')

    _CLASSES = {}

    @classmethod
    def register_type(cls, _class, _type=None):
        if _type is None:
            _type = _class._type
        existing = cls._CLASSES.get(_type)
        if existing:
            module = existing.__module__
            name = existing.__name__
            msg = f'Type "{_type}" already registered by {module}.{name}'
            raise RecordException(msg)
        cls._CLASSES[_type] = _class

    @classmethod
    def registered_types(cls):
        return cls._CLASSES

    @classmethod
    def new(cls, zone, name, data, source=None, lenient=False):
        reasons = []
        try:
            name = idna_encode(str(name))
        except IdnaError as e:
            # convert the error into a reason
            reasons.append(str(e))
            name = str(name)

        if ' ' in name or '\t' in name:
            reasons.append('invalid record, whitespace is not allowed')

        fqdn = f'{name}.{zone.name}' if name else zone.name
        try:
            _type = data['type']
        except KeyError:
            raise Exception(f'Invalid record {idna_decode(fqdn)}, missing type')
        try:
            _class = cls._CLASSES[_type]
        except KeyError:
            raise Exception(f'Unknown record type: "{_type}"')
        reasons.extend(_class.validate(name, fqdn, data))
        try:
            lenient |= data['octodns']['lenient']
        except KeyError:
            pass
        if reasons:
            if lenient:
                cls.log.warning(ValidationError.build_message(fqdn, reasons))
            else:
                raise ValidationError(fqdn, reasons)
        return _class(zone, name, data, source=source)

    @classmethod
    def validate(cls, name, fqdn, data):
        reasons = []
        if name == '@':
            reasons.append('invalid name "@", use "" instead')
        n = len(fqdn)
        if n > 253:
            reasons.append(
                f'invalid fqdn, "{idna_decode(fqdn)}" is too long at {n} '
                'chars, max is 253'
            )
        for label in name.split('.'):
            n = len(label)
            if n > 63:
                reasons.append(
                    f'invalid label, "{label}" is too long at {n}'
                    ' chars, max is 63'
                )
        # TODO: look at the idna lib for a lot more potential validations...
        try:
            ttl = int(data['ttl'])
            if ttl < 0:
                reasons.append('invalid ttl')
        except KeyError:
            reasons.append('missing ttl')
        try:
            if data['octodns']['healthcheck']['protocol'] not in (
                'HTTP',
                'HTTPS',
                'TCP',
            ):
                reasons.append('invalid healthcheck protocol')
        except KeyError:
            pass
        return reasons

    @classmethod
    def from_rrs(cls, zone, rrs, lenient=False):
        # group records by name & type so that multiple rdatas can be combined
        # into a single record when needed
        grouped = defaultdict(list)
        for rr in rrs:
            grouped[(rr.name, rr._type)].append(rr)

        records = []
        # walk the grouped rrs converting each one to data and then create a
        # record with that data
        for _, rrs in sorted(grouped.items()):
            rr = rrs[0]
            name = zone.hostname_from_fqdn(rr.name)
            _class = cls._CLASSES[rr._type]
            data = _class.data_from_rrs(rrs)
            record = Record.new(zone, name, data, lenient=lenient)
            records.append(record)

        return records

    def __init__(self, zone, name, data, source=None):
        self.zone = zone
        if name:
            # internally everything is idna
            self.name = idna_encode(str(name))
            # we'll keep a decoded version around for logs and errors
            self.decoded_name = idna_decode(self.name)
        else:
            self.name = self.decoded_name = name
        self.log.debug(
            '__init__: zone.name=%s, type=%11s, name=%s',
            zone.decoded_name,
            self.__class__.__name__,
            self.decoded_name,
        )
        self.source = source
        self.ttl = int(data['ttl'])

        self._octodns = data.get('octodns', {})

    def _data(self):
        return {'ttl': self.ttl}

    @property
    def data(self):
        return self._data()

    @property
    def fqdn(self):
        # TODO: these should be calculated and set in __init__ rather than on
        # each use
        if self.name:
            return f'{self.name}.{self.zone.name}'
        return self.zone.name

    @property
    def decoded_fqdn(self):
        if self.decoded_name:
            return f'{self.decoded_name}.{self.zone.decoded_name}'
        return self.zone.decoded_name

    @property
    def ignored(self):
        return self._octodns.get('ignored', False)

    @property
    def excluded(self):
        return self._octodns.get('excluded', [])

    @property
    def included(self):
        return self._octodns.get('included', [])

    def healthcheck_host(self, value=None):
        healthcheck = self._octodns.get('healthcheck', {})
        if healthcheck.get('protocol', None) == 'TCP':
            return None
        return healthcheck.get('host', self.fqdn[:-1]) or value

    @property
    def healthcheck_path(self):
        healthcheck = self._octodns.get('healthcheck', {})
        if healthcheck.get('protocol', None) == 'TCP':
            return None
        try:
            return healthcheck['path']
        except KeyError:
            return '/_dns'

    @property
    def healthcheck_protocol(self):
        try:
            return self._octodns['healthcheck']['protocol']
        except KeyError:
            return 'HTTPS'

    @property
    def healthcheck_port(self):
        try:
            return int(self._octodns['healthcheck']['port'])
        except KeyError:
            return 443

    def changes(self, other, target):
        # We're assuming we have the same name and type if we're being compared
        if self.ttl != other.ttl:
            return Update(self, other)

    def copy(self, zone=None):
        data = self.data
        data['type'] = self._type
        data['octodns'] = self._octodns

        return Record.new(
            zone if zone else self.zone,
            self.name,
            data,
            self.source,
            lenient=True,
        )

    # NOTE: we're using __hash__ and ordering methods that consider Records
    # equivalent if they have the same name & _type. Values are ignored. This
    # is useful when computing diffs/changes.

    def __hash__(self):
        return f'{self.name}:{self._type}'.__hash__()

    def _equality_tuple(self):
        return (self.name, self._type)

    def __repr__(self):
        # Make sure this is always overridden
        raise NotImplementedError('Abstract base class, __repr__ required')


class ValuesMixin(object):
    @classmethod
    def validate(cls, name, fqdn, data):
        reasons = super().validate(name, fqdn, data)

        values = data.get('values', data.get('value', []))

        reasons.extend(cls._value_type.validate(values, cls._type))

        return reasons

    @classmethod
    def data_from_rrs(cls, rrs):
        # type and TTL come from the first rr
        rr = rrs[0]
        # values come from parsing the rdata portion of all rrs
        values = [cls._value_type.parse_rdata_text(rr.rdata) for rr in rrs]
        return {'ttl': rr.ttl, 'type': rr._type, 'values': values}

    def __init__(self, zone, name, data, source=None):
        super().__init__(zone, name, data, source=source)
        try:
            values = data['values']
        except KeyError:
            values = [data['value']]
        self.values = sorted(self._value_type.process(values))

    def changes(self, other, target):
        if self.values != other.values:
            return Update(self, other)
        return super().changes(other, target)

    def _data(self):
        ret = super()._data()
        if len(self.values) > 1:
            values = [getattr(v, 'data', v) for v in self.values if v]
            if len(values) > 1:
                ret['values'] = values
            elif len(values) == 1:
                ret['value'] = values[0]
        elif len(self.values) == 1:
            v = self.values[0]
            if v:
                ret['value'] = getattr(v, 'data', v)

        return ret

    @property
    def rrs(self):
        return (
            self.fqdn,
            self.ttl,
            self._type,
            [v.rdata_text for v in self.values],
        )

    def __repr__(self):
        values = "', '".join([str(v) for v in self.values])
        klass = self.__class__.__name__
        return f"<{klass} {self._type} {self.ttl}, {self.decoded_fqdn}, ['{values}']>"


class ValueMixin(object):
    @classmethod
    def validate(cls, name, fqdn, data):
        reasons = super().validate(name, fqdn, data)
        reasons.extend(
            cls._value_type.validate(data.get('value', None), cls._type)
        )
        return reasons

    @classmethod
    def data_from_rrs(cls, rrs):
        # single value, so single rr only...
        rr = rrs[0]
        return {
            'ttl': rr.ttl,
            'type': rr._type,
            'value': cls._value_type.parse_rdata_text(rr.rdata),
        }

    def __init__(self, zone, name, data, source=None):
        super().__init__(zone, name, data, source=source)
        self.value = self._value_type.process(data['value'])

    def changes(self, other, target):
        if self.value != other.value:
            return Update(self, other)
        return super().changes(other, target)

    def _data(self):
        ret = super()._data()
        if self.value:
            ret['value'] = getattr(self.value, 'data', self.value)
        return ret

    @property
    def rrs(self):
        return self.fqdn, self.ttl, self._type, [self.value.rdata_text]

    def __repr__(self):
        klass = self.__class__.__name__
        return f'<{klass} {self._type} {self.ttl}, {self.decoded_fqdn}, {self.value}>'
