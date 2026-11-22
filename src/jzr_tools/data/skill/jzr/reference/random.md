# jzr random

## jzr random int

```text
Usage: jzr random int [OPTIONS] [MIN MAX]

  Random whole numbers from MIN to MAX, both included (default 1 100).

  Negative bounds work as they are: jzr random int -10 10.

Options:
  -n, --count INTEGER  How many numbers.  [default: 1]
  --unique             No number twice.
```

Examples:

- `jzr random int 1 6 -n 3` - rolls three dice

## jzr random pick

```text
Usage: jzr random pick [OPTIONS] [ITEMS]...

  Pick random ITEMS; without ITEMS, the lines of stdin.

Options:
  -n, --count INTEGER  How many items.  [default: 1]
  --repeat             Allow the same item more than once.
```

Examples:

- `jzr random pick red green blue` - picks one

## jzr random shuffle

```text
Usage: jzr random shuffle [OPTIONS] [ITEMS]...

  Print ITEMS in random order; without ITEMS, the lines of stdin.
```

Examples:

- `jzr random shuffle a b c` - prints them in random order
