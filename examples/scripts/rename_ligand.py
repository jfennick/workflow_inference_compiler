import argparse


parser = argparse.ArgumentParser()
parser.add_argument('--input_path', required=True)
parser.add_argument('--output_path', required=False)
parser.add_argument('--ligand_name', required=True)
parser.add_argument('--ligand_new_name', required=False, default='MOL')
args = parser.parse_args()

with open(args.input_path, mode='r', encoding='utf-8') as f:
    lines = f.readlines()

lines_new = []
for line in lines:
    l = line
    if args.ligand_name in l:
        l = l.replace(args.ligand_name, args.new_name)
    lines_new.append(l)

with open(args.output_path, mode='w', encoding='utf-8') as f:
    f.writelines(lines_new)
