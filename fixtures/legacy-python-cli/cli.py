import click

import inventory


@click.group()
def cli():
    """Legacy inventory command line."""


@cli.command()
@click.argument("name")
@click.argument("qty", type=int)
def add(name, qty):
    click.echo(inventory.add_item(name, qty))


@cli.command()
def total():
    click.echo(inventory.total_items())


@cli.command()
@click.argument("cmd")
def report(cmd):
    click.echo(inventory.run_report(cmd))


if __name__ == "__main__":
    cli()
