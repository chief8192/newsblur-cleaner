# newsblur-cleaner

Python utility for cleaning up NewsBlur feeds. Options include:

- De-duplicating based on story title.
- De-duplicating based on permalink.
- Purging stories that are older than a specified cutoff.
- Limiting feeds to a specified maximum number of stories.
- Purging stories whose titles don't match a specified language.

## Usage

### Using `python3`

```shell
$ python3 ./newsblur_cleaner.py \
    --username=<USERNAME> \
    --password=<PASSWORD> \
    <optional_arguments>
```

### Using `bazel`

```shell
$ bazel run :newsblur_cleaner \
    -- \
    --username=<USERNAME> \
    --password=<PASSWORD> \
    <optional_arguments>
```

### Using Pythonista

`newsblur_cleaner.py` can be run within
[Pythonista](https://omz-software.com/pythonista/), with the exception of the
`--language` flag, due to lack of
[`langdetect`](https://pypi.org/project/langdetect/) support.
