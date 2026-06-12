# Root Makefile — local dev convenience.
# Doc 23 dev-up/dev-down + W-3.1 ttyd-mock + PR-H smoke targets, all in one file.

.PHONY: ttyd-mock-up ttyd-mock-down help smoke smoke-cassette smoke-resilience smoke-all dev-up dev-down dev-status

help:
	@echo "Local dev targets:"
	@echo "  dev-up             — start C1 + console in background (bash + trap)"
	@echo "  dev-down           — stop both services"
	@echo "  dev-status         — show whether C1/console pids are alive"
	@echo "ttyd targets:"
	@echo "  ttyd-mock-up       — start docker ttyd on :7681 (W-3.1)"
	@echo "  ttyd-mock-down     — stop ttyd container"
	@echo "Smoke targets:"
	@echo "  smoke              — quick in-process cassette (M1 A1+A3+A5)"
	@echo "  smoke-cassette     — same as smoke (alias)"
	@echo "  smoke-resilience   — R1/R2/R3 (PR-D state machine merged; live)"
	@echo "  smoke-all          — full smoke + resilience"

dev-up:
	@bash scripts/dev-up.sh

dev-down:
	@bash scripts/dev-down.sh

dev-status:
	@for svc in c1 console; do \
	  pidfile=".dev-logs/$$svc.pid"; \
	  if [ -f "$$pidfile" ] && kill -0 "$$(cat $$pidfile)" 2>/dev/null; then \
	    echo "  $$svc: running (pid $$(cat $$pidfile))"; \
	  else \
	    echo "  $$svc: down"; \
	  fi; \
	done

ttyd-mock-up:
	@bash scripts/dev-ttyd-mock.sh

ttyd-mock-down:
	@bash scripts/dev-ttyd-mock-stop.sh

# `--extra dev` resolves control's full closure (workspace siblings + pytest
# extras) from its pyproject, instead of a hand-copied --with list that drifts
# from the real deps. #356.
smoke smoke-cassette:
	cd packages/agent-platform-control && \
	  uv run --extra dev \
	    pytest -q -p no:cacheprovider --no-cov \
	      tests/integration/test_e2e_software.py

smoke-resilience:
	@echo "R1/R2/R3 active post PR-D state machine merge."
	$(MAKE) smoke

smoke-all: smoke smoke-resilience
