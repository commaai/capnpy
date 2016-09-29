"""
Structor -> struct ctor -> struct construtor :)
"""

import struct
from capnpy.schema import Field, Type

class Unsupported(Exception):
    pass

class Structor(object):
    """
    Create a struct constructor.

    Some terminology:

      - fields: the list of schema.Field objects, as it appears in
                schema.Node.struct

      - argnames: the name of arguments taken by the ctor

      - params: [(argname, default)], for each argname in argnames

      - llfields: flattened list of "low level fields", as they are used to
                  build the buffer.  Normally, each field corresponds to one
                  llfield, but each group field has many llfields

      - llnames: {llfield: llname}; the llname if the name of the variable
                 used to contain the value of each llfield. For llfields
                 inside groups, it is "groupname_fieldname".

    In case of groups, we generate code to map the single argname into the
    many llfields: this is called "unpacking"
    """

    _unsupported = None

    def __init__(self, m, name, data_size, ptrs_size, fields,
                 tag_offset=None, tag_value=None):
        self.m = m
        self.name = name
        self.data_size = data_size
        self.ptrs_size = ptrs_size
        self.tag_offset = tag_offset
        self.tag_value = tag_value
        #
        self.argnames = []    # the arguments accepted by the ctor, in order
        self.params = []
        self.llfields = []    # "low level fields", passed to StructBuilder
        self.llname = {}      # for plain fields is simply f.name, but in case
                              # of groups it's groupname_fieldname
        self.groups = []
        try:
            self.init_fields(fields)
            self.fmt = self._compute_format()
        except Unsupported as e:
            self.argnames = []
            self._unsupported = e.message

    def init_fields(self, fields):
        defaults = []
        for f in fields:
            if f.is_group():
                fname = self._append_group(f)
                self.argnames.append(fname)
                defaults.append('None') # XXX fixme
            else:
                fname = self._append_field(f)
                default = f.slot.defaultValue.as_pyobj()
                self.argnames.append(fname)
                defaults.append(str(default))

        assert len(self.argnames) == len(defaults)
        self.params = zip(self.argnames, defaults)

        if self.tag_offset is not None:
            # add a field to represent the tag, but don't add it to argnames,
            # as it's implicit
            tag_offset = self.tag_offset/2 # from bytes to multiple of int16
            tag_field = Field.new_slot('__which__', tag_offset, Type.new_int16())
            self._append_field(tag_field)


    def _append_field(self, f, prefix=None):
        name = self.m._field_name(f)
        if prefix:
            name = '%s_%s' % (prefix, name)
        self.llfields.append(f)
        self.llname[f] = name
        return name

    def _append_group(self, f):
        nullable = f.is_nullable(self.m)
        if nullable:
            nullable.check(self.m)
        groupname = self.m._field_name(f)
        group = self.m.allnodes[f.group.typeId]
        self.groups.append((f, groupname, group))
        for f in group.struct.fields:
            if f.is_void():
                continue
            fname = self.m._field_name(f)
            self.llfields.append(f)
            self.llname[f] = '%s_%s' % (groupname, fname)
        return groupname

    def _slot_offset(self, f):
        offset = f.slot.offset * f.slot.get_size()
        if f.slot.type.is_pointer():
            offset += self.data_size*8
        return offset

    def _compute_format(self):
        total_length = (self.data_size + self.ptrs_size)*8
        fmt = ['x'] * total_length

        def set(offset, t):
            fmt[offset] = t
            size = struct.calcsize(t)
            for i in range(offset+1, offset+size):
                fmt[i] = None

        for f in self.llfields:
            if not f.is_slot() or f.slot.type.is_bool():
                raise Unsupported('Unsupported field type: %s' % f.shortrepr())
            elif f.is_void():
                continue
            set(self._slot_offset(f), f.slot.get_fmt())
        #
        # remove all the Nones
        fmt = [ch for ch in fmt if ch is not None]
        fmt = ''.join(fmt)
        assert struct.calcsize(fmt) == total_length
        return fmt

    def declare(self, code):
        if self._unsupported is not None:
            return self._decl_unsupported(code)
        else:
            return self._decl_ctor(code)

    def _decl_unsupported(self, code):
        code.w('@staticmethod')
        with code.def_(self.name, self.argnames, '*args', '**kwargs'):
            code.w('raise NotImplementedError({msg})', msg=repr(self._unsupported))

    def _decl_ctor(self, code):
        ## generate a constructor which looks like this
        ## @staticmethod
        ## def ctor(x=0, y=0, z=None):
        ##     builder = _StructBuilder('qqq')
        ##     z = builder.alloc_text(16, z)
        ##     buf = builder.build(x, y)
        ##     return buf
        #
        # the parameters have the same order as fields
        argnames = self.argnames

        # for for building, we sort them by offset
        self.llfields.sort(key=lambda f: self._slot_offset(f))
        buildnames = [self.llname[f] for f in self.llfields if not f.is_void()]

        if len(argnames) != len(set(argnames)):
            raise ValueError("Duplicate field name(s): %s" % argnames)
        code.w('@staticmethod')
        with code.def_(self.name, self.params):
            code.w('builder = _StructBuilder({fmt})', fmt=repr(self.fmt))
            if self.tag_value is not None:
                code.w('__which__ = {tag_value}', tag_value=int(self.tag_value))
            #
            for f, groupname, group in self.groups:
                if f.is_nullable(self.m):
                    self._unpack_nullable(code, groupname)
                else:
                    self._unpack_group(code, groupname, group)
            #
            for f in self.llfields:
                if f.is_text():
                    self._field_text(code, f)
                elif f.is_data():
                    self._field_data(code, f)
                elif f.is_struct():
                    self._field_struct(code, f)
                elif f.is_list():
                    self._field_list(code, f)
                elif f.is_primitive() or f.is_enum():
                    self._field_primitive(code, f)
                elif f.is_void():
                    pass # nothing to do
                else:
                    code.w("raise NotImplementedError('Unsupported field type: {f}')",
                           f=f.shortrepr())
                #
            code.w('buf =', code.call('builder.build', buildnames))
            code.w('return buf')

    def _unpack_group(self, code, groupname, group):
        argnames = [self.llname[f] for f in group.struct.fields
                    if not f.is_void()]
        code.w('{args}, = {groupname}',
               args=code.args(argnames), groupname=groupname)

    def _unpack_nullable(self, code, groupname):
        # def __init__(self, ..., x, ...):
        #     ...
        #     if x is None:
        #         x_is_null = 1
        #         x_value = 0
        #     else:
        #         x_is_null = 0
        #         x_value = x
        #
        ns = code.new_scope()
        ns.fname = groupname
        ns.ww(
        """
            if {fname} is None:
                {fname}_is_null = 1
                {fname}_value = 0
            else:
                {fname}_is_null = 0
                {fname}_value = {fname}
        """)

    def _field_text(self, code, f):
        fname = self.llname[f]
        code.w('{arg} = builder.alloc_text({offset}, {arg})',
               arg=fname, offset=self._slot_offset(f))

    def _field_data(self, code, f):
        fname = self.llname[f]
        code.w('{arg} = builder.alloc_data({offset}, {arg})',
               arg=fname, offset=self._slot_offset(f))

    def _field_struct(self, code, f):
        fname = self.llname[f]
        offset = self._slot_offset(f)
        structname = f.slot.type.runtime_name(self.m)
        code.w('{arg} = builder.alloc_struct({offset}, {structname}, {arg})',
               arg=fname, offset=offset, structname=structname)

    def _field_list(self, code, f):
        ns = code.new_scope()
        ns.fname = self.llname[f]
        ns.offset = self._slot_offset(f)
        itemtype = f.slot.type.list.elementType
        ns.itemtype = itemtype.runtime_name(self.m)
        #
        if itemtype.is_primitive():
            ns.listcls = '_PrimitiveList'
        elif itemtype.is_text():
            ns.listcls = '_StringList'
        elif itemtype.is_struct():
            ns.listcls = '_StructList'
        else:
            raise ValueError('Unknown item type: %s' % item_type)
        #
        ns.w('{fname} = builder.alloc_list({offset}, {listcls}, {itemtype}, {fname})')

    def _field_primitive(self, code, f):
        if f.slot.hadExplicitDefault:
            fname = self.llname[f]
            ns = code.new_scope()
            ns.arg = fname
            ns.default_ = f.slot.defaultValue.as_pyobj()
            ns.w('{arg} ^= {default_}')
