#!/usr/bin/env cwl-runner
cwlVersion: v1.0

class: CommandLineTool

label: Run a python3 script

doc: |-
  Run a python3 script

baseCommand: python3

hints:
  DockerRequirement:
    dockerPull: ndonyapour/scripts

inputs:
  script:
    type: string
    inputBinding:
      position: 1

  input_pdb_path:
    type: File
    format:
    - edam:format_1476 # pdb
    inputBinding:
      position: 2
      prefix: --input_path

  output_pdb_path:
    label: Path to the output file
    doc: |-
      Path to the output file
    type: string
    format:
    - edam:format_1476 # pdb
    inputBinding:
      position: 3
      prefix: --output_path
    default: system.pdb
    
  ligand_name:
    label: The name of the ligand
    doc: |-
      The name of the ligand
      Type: string?
    type: string?
    format:
    - edam:format_2330 # 'Textual format'
    inputBinding:
      position: 4
      prefix: --ligand_name

  ligand_new_name:
    label: The new name of the ligand
    doc: |-
      The new name of the ligand
      Type: string?
    type: string?
    format:
    - edam:format_2330 # 'Textual format'
    inputBinding:
      position: 5
      prefix: --ligand_new_name

outputs:
  output_pdb_path:
    label: Path to the output file
    doc: |-
      Path to the output file
    type: File
    format: edam:format_1476 # pdb
    outputBinding:
      glob: $(inputs.output_pdb_path)

$namespaces:
  edam: https://edamontology.org/

$schemas:
- https://raw.githubusercontent.com/edamontology/edamontology/master/EDAM_dev.owl