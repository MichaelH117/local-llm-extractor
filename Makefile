# POSIX task runner. Windows: run the same commands with .venv\Scripts\python (see README).
PY ?= .venv/bin/python

.PHONY: setup run test lint

setup:          ## create the venv and install the stack (Ollama itself is installed separately)
	python3 -m venv .venv && $(PY) -m pip install -r requirements.txt

run:            ## generate PDFs, extract with the local model, score, scrape, export
	$(PY) -m pipeline.run

test:           ## full run plus pytest, recorded in the README
	$(PY) -m pipeline.run --test

lint:
	$(PY) -m ruff check .
