import yaml, sys
try:
    with open(sys.argv[1], 'r') as f:
        data = yaml.safe_load(f)
    print("YAML syntax OK. Top-level keys:", list(data.keys()))
except yaml.YAMLError as e:
    print("YAML ERROR:", e)
    sys.exit(1)
