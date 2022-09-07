import glob
import subprocess as sub
import sys
import os
from pathlib import Path
from typing import Dict

import graphviz
import networkx as nx
import yaml

from . import ast, auto_gen_header, cli, compiler, inference, labshare, utils
from .schemas import wic_schema
from .wic_types import Cwl, GraphData, GraphReps, StepId, Tool, Tools, Yaml, YamlTree


def get_tools_cwl(cwl_dirs_file: Path) -> Tools:
    """Uses glob() to find all of the CWL CommandLineTool definition files within any subdirectory of cwl_dir

    Args:
        cwl_dirs_file (Path): The subdirectories in which to search for CWL CommandLineTools

    Returns:
        Tools: The CWL CommandLineTool definitions found using glob()
    """
    utils.copy_config_files()
    # Load ALL of the tools.
    tools_cwl: Tools = {}
    cwl_dirs = utils.read_lines_pairs(cwl_dirs_file)
    for plugin_ns, cwl_dir in cwl_dirs:
        # "PurePath.relative_to() requires self to be the subpath of the argument, but os.path.relpath() does not."
        # See https://docs.python.org/3/library/pathlib.html#id4 and
        # See https://stackoverflow.com/questions/67452690/pathlib-path-relative-to-vs-os-path-relpath
        cwl_dir_rel = os.path.relpath(cwl_dir) # w.r.t. current working directory
        pattern_cwl = str(Path(cwl_dir_rel) / '**/*.cwl')
        #print(pattern_cwl)
        # Note that there is a current and a legacy copy of each cwl file for each tool.
        # The only difference appears to be that some legacy parameters are named
        # *_file as opposed to *_path. Since glob does NOT return the results in
        # any particular order, and since we are using stem as our dict key, current
        # files may be overwritten with legacy files (and vice versa), resulting in
        # an inconsistent naming scheme. Since legacy files are stored in an additional
        # subdirctory, if we sort the paths by descending length, we can overwrite
        # the dict entries of the legacy files.
        cwl_paths_sorted = sorted(glob.glob(pattern_cwl, recursive=True), key=len, reverse=True)
        Path('autogenerated/schemas/tools/').mkdir(parents=True, exist_ok=True)
        if len(cwl_paths_sorted) == 0:
            print(f'Warning! No cwl files found in {cwl_dir_rel}. Check cwl_dirs.txt')
        for cwl_path_str in cwl_paths_sorted:
            #print(cwl_path)
            try:
                with open(cwl_path_str, mode='r', encoding='utf-8') as f:
                    tool: Cwl = yaml.safe_load(f.read())
                stem = Path(cwl_path_str).stem
                # print(stem)
                # Add / overwrite stdout and stderr
                tool.update({'stdout': f'{stem}.out'})
                tool.update({'stderr': f'{stem}.err'})
                step_id = StepId(stem, plugin_ns)
                tools_cwl[step_id] = Tool(cwl_path_str, tool)
                #print(tool)
            except yaml.scanner.ScannerError as s_e:
                pass
                # There are two cwl files that throw this error, but they are both legacy, so...
                #print(cwl_path)
                #print(s_e)
            #utils.make_tool_dag(stem, (cwl_path_str, tool))
    return tools_cwl


def get_yml_paths(yml_dirs_file: Path) -> Dict[str, Dict[str, Path]]:
    """Uses glob() to recursively find all of the yml workflow definition files
    within any subdirectory of each yml_dir in yml_dirs_file.
    NOTE: This function assumes all yml files found are workflow definition files,
    so do not mix regular yml files and workflow files in the same root directory.
    Moreover, each yml_dir should be disjoint; do not use both '.' and './subdir'!

    Args:
        yml_dirs_file (Path): The subdirectories in which to search for yml files

    Returns:
        Dict[str, Dict[str, Path]]: A dict containing the filepath stem and filepath of each yml file
    """
    utils.copy_config_files()
    yml_dirs = utils.read_lines_pairs(yml_dirs_file)
    # Glob all of the yml files too, so we don't have to deal with relative paths.
    yml_paths_all: Dict[str, Dict[str, Path]] = {}
    for yml_namespace, yml_dir in yml_dirs:
        # "PurePath.relative_to() requires self to be the subpath of the argument, but os.path.relpath() does not."
        # See https://docs.python.org/3/library/pathlib.html#id4 and
        # See https://stackoverflow.com/questions/67452690/pathlib-path-relative-to-vs-os-path-relpath
        yml_dir_rel = os.path.relpath(yml_dir) # w.r.t. current working directory
        pattern_yml = str(Path(yml_dir_rel) / '**/*.yml')
        yml_paths_sorted = sorted(glob.glob(pattern_yml, recursive=True), key=len, reverse=True)
        if len(yml_paths_sorted) == 0:
            print(f'Warning! No yml files found in {yml_dir_rel}. Check yml_dirs.txt')
        yml_paths = {}
        for yml_path_str in yml_paths_sorted:
            # Exclude our autogenerated inputs files
            if '_inputs' not in yml_path_str:
                yml_path = Path(yml_path_str)
                yml_paths[yml_path.stem] = yml_path
        # Check for existing entry (so we can split a single
        # namespace across multiple lines in yml_dirs.txt)
        ns_dict = yml_paths_all.get(yml_namespace, {})
        yml_paths_all[yml_namespace] = {**ns_dict, **yml_paths}

    return yml_paths_all


def main() -> None:
    """See docs/userguide.md"""
    args = cli.parser.parse_args()

    tools_cwl = get_tools_cwl(args.cwl_dirs_file)
    utils.make_plugins_dag(tools_cwl, args.graph_dark_theme)
    yml_paths = get_yml_paths(args.yml_dirs_file)

    # Perform initialization via mutating global variables (This is not ideal)
    compiler.inference_rules = dict(utils.read_lines_pairs(Path('inference_rules.txt')))
    inference.renaming_conventions = utils.read_lines_pairs(Path('renaming_conventions.txt'))

    # Generate schemas for validation and vscode IntelliSense code completion
    yaml_stems = utils.flatten([list(p) for p in yml_paths.values()])
    validator = wic_schema.get_validator(tools_cwl, yaml_stems, write_to_disk=True)
    if args.generate_schemas_only:
        print('Finished generating schemas. Exiting.')
        sys.exit(0)

    yaml_path = args.yaml

    # Load the high-level yaml root workflow file.
    with open(yaml_path, mode='r', encoding='utf-8') as y:
        root_yaml_tree: Yaml = yaml.safe_load(y.read())
    Path('autogenerated/').mkdir(parents=True, exist_ok=True)
    wic = {'wic': root_yaml_tree.get('wic', {})}
    plugin_ns = wic['wic'].get('namespace', 'global')
    step_id = StepId(yaml_path, plugin_ns)
    y_t = YamlTree(step_id, root_yaml_tree)
    yaml_tree_raw = ast.read_ast_from_disk(y_t, yml_paths, tools_cwl, validator)
    # Write the combined workflow (with all subworkflows as children) to disk.
    with open(f'autogenerated/{Path(yaml_path).stem}_tree_raw.yml', mode='w', encoding='utf-8') as f:
        f.write(yaml.dump(yaml_tree_raw.yml))
    yaml_tree = ast.merge_yml_trees(yaml_tree_raw, {}, tools_cwl)
    with open(f'autogenerated/{Path(yaml_path).stem}_tree_merged.yml', mode='w', encoding='utf-8') as f:
        f.write(yaml.dump(yaml_tree.yml))

    # TODO: Test new inlineing code
    if args.cwl_inline_subworkflows:
        while True:
            # Inlineing changes the namespaces, so we have to get new namespaces after each inlineing operation.
            namespaces_list = ast.get_inlineable_subworkflows(yaml_tree, tools_cwl, False, [])
            if namespaces_list == []:
                break

            #print('inlineing', namespaces_list[0])
            yaml_tree = ast.inline_subworkflow(yaml_tree, tools_cwl, namespaces_list[0])

        with open(f'autogenerated/{Path(yaml_path).stem}_tree_merged_inlined.yml', mode='w', encoding='utf-8') as f:
            f.write(yaml.dump(yaml_tree.yml))

    rootgraph = graphviz.Digraph(name=yaml_path)
    rootgraph.attr(newrank='True') # See graphviz layout comment above.
    rootgraph.attr(bgcolor="transparent") # Useful for making slides
    font_edge_color = 'black' if args.graph_dark_theme else 'white'
    rootgraph.attr(fontcolor=font_edge_color)

    # This can be used to visually 'inline' all subworkflows (but NOT the CWL).
    # rootgraph.attr(style='invis')
    # Note that since invisible objects still affect the graphviz layout (by design),
    # this can be used to control the layout of the individual nodes, even if
    # you don't necessarily want subworkflows.

    #rootgraph.attr(rankdir='LR') # When --graph_inline_depth 1, this usually looks better.
    with rootgraph.subgraph(name=f'cluster_{yaml_path}') as subgraph_gv:
        # get the label (if any) from the workflow
        step_i_wic_graphviz = yaml_tree.yml.get('wic', {}).get('graphviz', {})
        label = step_i_wic_graphviz.get('label', yaml_path)
        subgraph_gv.attr(label=label)
        subgraph_gv.attr(color='lightblue')  # color of cluster subgraph outline
        subgraph_nx = nx.DiGraph()
        graphdata = GraphData(yaml_path)
        subgraph = GraphReps(subgraph_gv, subgraph_nx, graphdata)
        compiler_info = compiler.compile_workflow(yaml_tree, args, [], [subgraph], {}, {},
                                                  tools_cwl, True, relative_run_path=True)
        rose_tree = compiler_info.rose

    utils.write_to_disk(rose_tree, Path('autogenerated/'), relative_run_path=True)

    if args.cwl_run_slurm:
        # Inline compiled CWL if necessary, i.e. inline across scattering boundaries.
        # NOTE: Since we need to distribute scattering operations across all dependencies,
        # and due to inference, this cannot be done before compilation.
        rose_tree = ast.inline_subworkflow_cwl(rose_tree)
        utils.write_to_disk(rose_tree, Path('inlined/'), relative_run_path=True)
        import sys
        sys.exit(0)
        #labshare.upload_all(rose_tree, tools_cwl, args, True)

    # Render the GraphViz diagram
    rootgraph.render(format='png') # Default pdf. See https://graphviz.org/docs/outputs/
    #rootgraph.view() # viewing does not work on headless machines (and requires xdg-utils)

    if args.cwl_run_local:
        yaml_inputs = rose_tree.data.workflow_inputs_file
        stage_input_files(yaml_inputs, Path(args.yaml).parent.absolute())

        yaml_stem = Path(args.yaml).stem
        yaml_stem = yaml_stem + '_inline' if args.cwl_inline_subworkflows else yaml_stem
        parallel = ['--parallel'] if args.parallel else []
        # NOTE: --parallel is required for real-time analysis / real-time plots,
        # but it seems to cause hanging with Docker for Mac. The hanging seems
        # to be worse when using parallel scattering.
        # NOTE: Using --leave-outputs because https://github.com/dnanexus/dx-cwl/issues/20
        cmd = ['cwltool'] + parallel + ['--quiet', '--leave-tmpdir', '--leave-outputs', '--cachedir', args.cachedir, '--outdir', 'outdir',
               f'autogenerated/{yaml_stem}.cwl', f'autogenerated/{yaml_stem}_inputs.yml']
        print('Running ' + ' '.join(cmd))
        sub.run(cmd, check=False)


def stage_input_files(yml_inputs: Yaml, root_yml_dir_abs: Path,
                      relative_run_path: bool = True, throw: bool = True) -> None:
    """Copies the input files in yml_inputs to the working directory.

    Args:
        yml_inputs (Yaml): The yml inputs file for the root workflow.
        root_yml_dir_abs (Path): The absolute path of the root workflow yml file.
        relative_run_path (bool): Controls whether to use subdirectories or\n
        just one directory when writing the compiled CWL files to disk
        throw (bool): Controls whether to raise/throw a FileNotFoundError.

    Raises:
        FileNotFoundError: If throw and it any of the input files do not exist.
    """
    sub.run(['./download_data.sh'], check=True, cwd=Path('examples/data/'))
    for key, val in yml_inputs.items():
        if isinstance(val, Dict) and val.get('class', '') == 'File':
            path = root_yml_dir_abs / Path(val['path'])
            if not path.exists() and throw:
                raise FileNotFoundError(f'Error! {path} does not exist!')

            relpath = Path('autogenerated/') if relative_run_path else Path('.')
            pathauto = relpath / Path(val['path']) # .name # NOTE: Use .name ?
            pathauto.parent.mkdir(parents=True, exist_ok=True)

            if path != pathauto:
                cmd = ['cp', str(path), str(pathauto)]
                proc = sub.run(cmd, check=False)


if __name__ == '__main__':
    main()
