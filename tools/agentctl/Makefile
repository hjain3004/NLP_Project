ROOT := $(shell git rev-parse --show-toplevel)
env:
	@echo 'export PYTHONPATH="$(ROOT)/tools/agentctl"'
test:
	@cd $(ROOT) && PYTHONPATH="$(ROOT)/tools/agentctl" \
	  python3 -m unittest discover -s tools/agentctl/tests -t tools/agentctl -v
check: test
	@cd $(ROOT) && PYTHONPATH="$(ROOT)/tools/agentctl" python3 -m agentctl doctor
