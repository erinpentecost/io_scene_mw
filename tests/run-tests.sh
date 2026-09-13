SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)


python3 ${SCRIPT_DIR}/smoke-test.py
python3 ${SCRIPT_DIR}/../cmd/dump_nif_tree/dump_nif_tree.py tests/tr_clannfear_lesser.nif > tests/smoke_output/tr_clannfear_lesser.json
python3 ${SCRIPT_DIR}/../cmd/dump_nif_tree/dump_nif_tree.py tests/xtr_clannfear_lesser.nif > tests/smoke_output/xtr_clannfear_lesser.json
python3 ${SCRIPT_DIR}/../cmd/dump_nif_tree/dump_nif_tree.py tests/smoke_output/anubis.nif > tests/smoke_output/anubis.json
