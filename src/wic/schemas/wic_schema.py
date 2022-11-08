import argparse
import json
from pathlib import Path
from unittest.mock import patch
import sys
from typing import Any, Dict, List

import networkx as nx
import graphviz
from jsonschema import RefResolver, Draft202012Validator
import yaml

import wic
from wic import ast, cli, compiler
from wic.wic_types import GraphData, GraphReps, NodeData, StepId, Yaml, YamlTree
from ..wic_types import Json, Tools
from .biobb import config_schemas


def default_schema(url: bool = False) -> Json:
    """A basic default schema (to avoid copy & paste).

    Args:
        url (bool, optional): Determines whether to include the $schema url. Defaults to False.

    Returns:
        Json: A basic default schema
    """
    schema: Json = {}
    schema['type'] = 'object'
    schema['additionalProperties'] = False
    if url:
        schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    return schema


def cwl_schema(name: str, cwl: Json, id_prefix: str) -> Json:
    """Generates a schema (including documentation) based on the inputs of a CWL CommandLineTool or Workflow.

    Args:
        name (str): The name of the CWL CommandLineTool or Workflow
        cwl (Json): The CWL CommandLineTool or Workflow
        id_prefix (str): Either the string 'tools' or 'workflows'

    Returns:
        Json: An autogenerated, documented schema based on the inputs of a CWL CommandLineTool or Workflow.
    """
    inputs_props: Json = {}
    #required = []
    for key, val in cwl['inputs'].items():
        inputs_props[key] = {}
        # Initialize special cases
        if key == 'config':
            inputs_props[key] = config_schemas.get(name, {})

        inputs_props[key]['title'] = val.get('label', '')
        inputs_props[key]['description'] = val.get('doc', '')

        valtype = val.get('type', '')
        # Determine required keys
        #if key == 'config' or not ('?' in valtype or 'default' in val):
        #    required.append(key)

        # Add type information, with exceptions
        if isinstance(valtype, str):
            valtype = valtype.replace('?', '')
        if (not (valtype in ['', 'File']) and # Json does not have a File type
            not (key == 'config') and name in config_schemas):  # Exclude config schemas
            inputs_props[key]['type'] = valtype

    # Do not mark properties which are required for CWL as required for yml,
    # because the whole point of inference is that we shouldn't have to!
    #if not required == []:
    #    inputs_props['required'] = required

    inputs = default_schema()
    inputs['properties'] = inputs_props

    step_name = default_schema()
    step_name['properties'] = {'in': inputs}

    schema = default_schema(url=True)
    # NOTE: See comment in get_validator(). Nonetheless, the vscode YAML extension
    # appears to be resolving ids w.r.t. relative local paths. jsonschema
    # (correctly) treats f'tools/{name}.json' as as uninterpreted string,
    # so instead of using name let's just use fake relative paths in ids.
    schema['$id'] = f'{id_prefix}/{name}.json'
    schema['title'] = cwl.get('label', '')
    schema['description'] = cwl.get('doc', '')
    prop_name = name + '.yml' if id_prefix == 'workflows' else name
    schema['properties'] = {prop_name: step_name}
    return schema


def wic_tag_schema() -> Json:
    """The schema of the (recursive) wic: metadata annotation tag.

    Returns:
        Json: The schema of the (recursive) wic: metadata annotation tag.
    """
    # NOTE: This schema needs to be recursive. Use dynamic anchors / references.
    # See https://json-schema.org/draft/2020-12/json-schema-core.html#dynamic-ref
    # and https://stackoverflow.com/questions/69728686/explanation-of-dynamicref-dynamicanchor-in-json-schema-as-opposed-to-ref-and

    graphviz_props: Json = {}
    graphviz_props['label'] = {'type': 'string'}
    graphviz_props['style'] = {'type': 'string'}
    graphviz_props['ranksame'] = {'type': 'array'}
    graphviz_props['ranksame']['items'] = {'type': 'string'}

    graphviz = default_schema()
    graphviz['properties'] = graphviz_props

    # Call recursive reference
    recursive_ref = {'$dynamicRef': '#wic'}
    in_props: Json = {} # TODO: Add yml specific properties

    scatter_props: Json = {} # TODO: Add yml specific properties
    scatterMethod_props: Json = {}

    choices = default_schema()
    choices['properties'] = {'in': in_props, 'wic': recursive_ref, 'scatter': scatter_props,
                             'scatterMethod': scatterMethod_props}

    # See https://json-schema.org/understanding-json-schema/reference/object.html#patternproperties
    # NOTE: This recursive schema is correct, as determined by jsonschema.validate()
    # However, it seems that the vscode YAML extension does not support recursive
    # schema. (IntelliSense works fine until the first instance of recursion.)
    # TODO: A workaround would be to autogenerate a specific schema for each
    # yml file. We should probably do this anyway for the in: tag.
    steps = default_schema()
    # additionalProperties = False still works with patternProperties FYI
    steps['patternProperties'] = {"\\([0-9]+, [A-Za-z0-9_\\.]+\\)": choices}

    #backends = default_schema()
    backends: Dict[Any, Any] = {}
    backends['type'] = 'object'
    backends['additionalProperties'] = True
    # TODO: Restrict the backend properties and make default_backend an enum

    namespace: Dict[Any, Any] = {}
    namespace['type'] = 'string'
    # namespace['enum'] = ...
    # TODO: Restrict the namespace properties to only those in yml_paths.txt

    backend = {'type': 'string'}
    default_backend = {'type': 'string'}
    inlineable = {'type': 'boolean'}

    environment_props: Json = {}
    environment_props['action'] = {'type': 'string'}
    environment_props['save_defs'] = {'type': 'array'}
    environment_props['save_defs']['items'] = {'type': 'string'}

    environment = default_schema()
    environment['properties'] = environment_props

    schema = default_schema(url=True)
    schema['$id'] = 'wic_tag'
    # Create recursive anchor
    schema['$dynamicAnchor'] = 'wic'
    schema['title'] = 'Metadata annotations'
    schema['description'] = 'Use steps: to recursively overload / pass parameters.\nUse graphviz: to modify the DAGs.'
    schema['properties'] = {'graphviz': graphviz, 'steps': steps, 'backend': backend,
                            'backends': backends, 'default_backend': default_backend,
                            'namespace': namespace, 'inlineable': inlineable, 'environment': environment}
    return schema


def wic_main_schema(tools_cwl: Tools, yml_stems: List[str],
                    schema_store: Dict[str, Json], use_yml_schema: bool) -> Json:
    """The main schema which is used to validate yml files.

    Args:
        tools_cwl (Tools): The CWL CommandLineTool definitions found using get_tools_cwl()
        yml_stems (List[str]): The names of the yml workflow definitions found using get_yml_paths()
        schema_store (Dict[str, Json]): A global mapping between ids and schemas
        use_yml_schema (bool): Determines whether to (recursively) include schemas from subworkflows.

    Returns:
        Json: The main schema which is used to validate yml files.
    """
    # NOTE: Use oneOf to allow {}, i.e. no explicit arguments
    tools_refs: List[Json] = [{'oneOf': [{'$ref': f'tools/{step_id.stem}.json'}, {}]} for step_id in tools_cwl]
    # NOTE: See comment in get_validator(). Nonetheless, the vscode YAML extension
    # appears to be resolving ids w.r.t. relative local paths. jsonschema
    # (correctly) treats f'tools/{name}.json' as as uninterpreted string,
    # so instead of using stem let's just use fake relative paths in ids.

    empty_schema: Json = {}
    # NOTE: We could/should re-validate after every AST modification. This will
    # require substantial code changes, so let's not worry about it for now.
    yml_schemas: List[Json] = []
    for yml_stem in yml_stems:
        yml_schema_id = f'workflows/{yml_stem}.json'
        """if use_yml_schema and yml_schema_id in schema_store:
            #print('schema_store', yml_stem)
            #print(yaml.dump(schema_store[yml_schema_id]))
            #yml_schemas.append(schema_store[yml_schema_id])
            yml_schemas.append({'oneOf': [{'$ref': yml_schema_id}, empty_schema]})
        else:
            yml_schemas.append({**default_schema(), 'properties': {f'{yml_stem}.yml': empty_schema}})"""
        yml_schemas.append({'oneOf': [{'$ref': yml_schema_id}, empty_schema]})
    #yml_schemas: List[Json] = [{**default_schema(), 'properties': {f'{stem}.yml': empty_schema}} for stem in yml_stems]
    #print('yml_schemas')
    #print(yaml.dump(yml_schemas))

    steps: Json = {}
    steps['type'] = 'array'
    steps['description'] = 'A list of workflow steps'
    steps['items'] = {'anyOf': tools_refs + yml_schemas, 'title': 'Valid workflow steps'}

    # TODO: Use the real CWL inputs schema
    inputs: Dict[Any, Any] = {}
    inputs['type'] = 'object'
    inputs['additionalProperties'] = True

    # TODO: Use the real CWL outputs schema
    outputs: Dict[Any, Any] = {}
    outputs['type'] = 'object'
    outputs['additionalProperties'] = True

    schema = default_schema(url=True)
    schema['$id'] = 'wic_main'
    schema['title'] = 'Validating against the Workflow Interence Compiler schema'
    #schema['description'] = ''
    #schema['required'] = ['steps']
    schema['properties'] = {'wic': wic_tag_schema(), 'steps': steps,
                            'inputs': inputs, 'outputs': outputs} # 'required': ['steps']

    return schema


def get_args(yml_path: str = '') -> argparse.Namespace:
    """This is used to get mock command line arguments.

    Returns:
        argparse.Namespace: The mocked command line arguments
    """
    testargs = ['wic', '--yaml', yml_path, '--cwl_output_intermediate_files', 'True']  # ignore --yaml
    # For now, we need to enable --cwl_output_intermediate_files. See comment in compiler.py
    with patch.object(sys, 'argv', testargs):
        args: argparse.Namespace = wic.cli.parser.parse_args()
    return args


def compile_workflow_generate_schema(yml_path_str: str, yml_path: Path,
                                     tools_cwl: Tools,
                                     yml_paths: Dict[str, Dict[str, Path]],
                                     schema_store: Dict[str, Json],
                                     validator: Draft202012Validator) -> None:
    """Compiles a workflow and generates a schema which (recursively) includes the inputs/outputs from subworkflows.

    Args:
        yml_path_str (str): The stem of the path to the yml file
        yml_path (Path): The path to the yml file
        tools_cwl (Tools): The CWL CommandLineTool definitions found using get_tools_cwl()
        yml_paths (Dict[str, Dict[str, Path]]): The yml workflow definitions found using get_yml_paths()
        schema_store (Dict[str, Json]): A global mapping between ids and schemas
        validator (Draft202012Validator): Used to validate the yml files against the autogenerated schema.
    """
    # First compile the workflow.
    # Load the high-level yaml workflow file.
    with open(yml_path, mode='r', encoding='utf-8') as y:
        root_yaml_tree: Yaml = yaml.safe_load(y.read())
    Path('autogenerated/').mkdir(parents=True, exist_ok=True)
    wic_tag = {'wic': root_yaml_tree.get('wic', {})}
    plugin_ns = wic_tag['wic'].get('namespace', 'global')
    step_id = StepId(yml_path_str, plugin_ns)
    y_t = YamlTree(step_id, root_yaml_tree)
    yaml_tree_raw = wic.ast.read_ast_from_disk(y_t, yml_paths, tools_cwl, validator)
    #with open(f'autogenerated/{Path(yml_path).stem}_tree_raw.yml', mode='w', encoding='utf-8') as f:
    #    f.write(yaml.dump(yaml_tree_raw.yml))
    yaml_tree = wic.ast.merge_yml_trees(yaml_tree_raw, {}, tools_cwl)
    #with open(f'autogenerated/{Path(yml_path).stem}_tree_merged.yml', mode='w', encoding='utf-8') as f:
    #    f.write(yaml.dump(yaml_tree.yml))

    graph_gv = graphviz.Digraph(name=f'cluster_{yml_path}')
    graph_gv.attr(newrank='True')
    graph_nx = nx.DiGraph()
    graphdata = GraphData(str(yml_path))
    graph = GraphReps(graph_gv, graph_nx, graphdata)
    compiler_info = wic.compiler.compile_workflow(yaml_tree, get_args(str(yml_path)), [], [graph], {}, {},
                                                    tools_cwl, True, relative_run_path=True, testing=True)
    rose_tree = compiler_info.rose
    sub_node_data: NodeData = rose_tree.data
    yaml_stem = sub_node_data.name

    #wic.utils.write_to_disk(rose_tree, Path('autogenerated/'), relative_run_path=True)
    schema_tool = cwl_schema(step_id.stem, sub_node_data.compiled_cwl, 'workflows')
    schema_store[schema_tool['$id']] = schema_tool

    with open(f'autogenerated/schemas/workflows/{step_id.stem}.json', mode='w', encoding='utf-8') as f:
        f.write(json.dumps(schema_tool, indent=2))


def get_validator(tools_cwl: Tools, yml_stems: List[str], schema_store: Dict[str, Json] = {},
                  use_yml_schema: bool = False, write_to_disk: bool = False) -> Draft202012Validator:
    """Generates the main schema used to check the yml files for correctness and returns a validator.

    Args:
        tools_cwl (Tools): The CWL CommandLineTool definitions found using get_tools_cwl()
        yml_stems (List[str]): The names of the yml workflow definitions found using get_yml_paths()
        schema_store (Dict[str, Json]): A global mapping between ids and schemas
        use_yml_schema (bool): Determines whether to (recursively) include schemas from subworkflows.
        write_to_disk (bool): Controls whether to write the schemas to disk.

    Returns:
        Draft202012Validator: A validator which is used to check the yml files for correctness.
    """
    for step_id, tool in tools_cwl.items():
        schema_tool = cwl_schema(step_id.stem, tool.cwl, 'tools')
        schema_store[schema_tool['$id']] = schema_tool
        if write_to_disk:
            with open(f'autogenerated/schemas/tools/{step_id.stem}.json', mode='w', encoding='utf-8') as f:
                f.write(json.dumps(schema_tool, indent=2))

    schema = wic_main_schema(tools_cwl, yml_stems, schema_store, use_yml_schema)
    schema_store[schema['$id']] = schema
    schema_store['wic_tag'] = wic_tag_schema()
    if write_to_disk:
        with open('autogenerated/schemas/wic.json', mode='w', encoding='utf-8') as f:
            f.write(json.dumps(schema, indent=2))

    # See https://stackoverflow.com/questions/53968770/how-to-set-up-local-file-references-in-python-jsonschema-document
    # The $ref tag refers to URIs defined in $id tags, NOT relative paths on
    # the local filesystem! We need to create a global mapping between ids and schemas
    # i.e. schema_store.
    resolver = RefResolver.from_schema(schema, store=schema_store)
    """ Use check_schema to 'first verify that the provided schema is
    itself valid, since not doing so can lead to less obvious error
    messages and fail in less obvious or consistent ways.'
    """
    # i.e. This should match 'https://json-schema.org/draft/2020-12/schema'
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, resolver=resolver)
    return validator
