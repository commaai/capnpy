import py
import pytest
import textwrap
from capnpy.compiler.fieldtree import FieldTree
from capnpy.testing.compiler.support import CompilerTest

class TestFieldTree(CompilerTest):

    schema = """
    @0xbf5147cbbecf40c1;
    struct Person {
        name :group {
            first @0 :Text;
            last @1 :Text;
        }
        address :group {
            street @2 :Text;
            position :group {
                x @3 :Int64 = 42;
                y @4 :Int64;
            }
        }
    }
    """

    def find_node(self, m, name):
        for node in m.allnodes.values():
            if node.shortname(m) == name:
                return node
        raise KeyError("Cannot find node %s" % name)

    def test_pprint(self, capsys):
        m = self.getm(self.schema)
        person = self.find_node(m, 'Person')
        tree = FieldTree(m, person.struct.fields)
        tree.pprint()
        out, err = capsys.readouterr()
        out = out.strip()
        assert out == textwrap.dedent("""
        <FieldTree>
            <Node name: group>
                <Node name_first: slot>
                <Node name_last: slot>
            <Node address: group>
                <Node address_street: slot>
                <Node address_position: group>
                    <Node address_position_x: slot>
                    <Node address_position_y: slot>
        """).strip()

    def test_allnodes(self):
        m = self.getm(self.schema)
        person = self.find_node(m, 'Person')
        tree = FieldTree(m, person.struct.fields)
        nodes = tree.allnodes()
        varnames = [node.varname for node in nodes]
        assert varnames == ['name',
                            'name_first',
                            'name_last',
                            'address',
                            'address_street',
                            'address_position',
                            'address_position_x',
                            'address_position_y']

    def test_allslots(self):
        m = self.getm(self.schema)
        person = self.find_node(m, 'Person')
        tree = FieldTree(m, person.struct.fields)
        nodes = tree.allslots()
        varnames = [node.varname for node in nodes]
        assert varnames == ['name_first',
                            'name_last',
                            'address_street',
                            'address_position_x',
                            'address_position_y']

    def test_default(self):
        m = self.getm(self.schema)
        person = self.find_node(m, 'Person')
        tree = FieldTree(m, person.struct.fields)
        items = [(node.varname, node.default) for node in tree.allnodes()]
        assert items == [
            ('name', '(None, None,)'),
            ('name_first', 'None'),
            ('name_last', 'None'),
            ('address', '(None, (42, 0,),)'),
            ('address_street', 'None'),
            ('address_position', '(42, 0,)'),
            ('address_position_x', '42'),
            ('address_position_y', '0'),
        ]
