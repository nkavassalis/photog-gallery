.PHONY: all clean default setup serve

default:
	python3 make.py

setup:
	python3 make.py setup

serve:
	python3 app.py

clean:
	rm -f .file_hashes.json
	rm -rf dist

all: clean default
