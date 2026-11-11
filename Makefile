.PHONY: check

check:
	./scripts/check-core-imports.sh
	swift build
	swift test
