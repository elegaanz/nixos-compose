import os
import sys
import time

import json

from io import open
from functools import update_wrapper

import click

from .platform import Grid5000Platform

# from .state import State

CONTEXT_SETTINGS = dict(
    auto_envvar_prefix="nixos_compose", help_option_names=["-h", "--help"]
)


def reraise(tp, value, tb=None):
    if value.__traceback__ is not tb:
        raise value.with_traceback(tb)
    raise value


class Context(object):
    def __init__(self):
        self.t0 = time.time()
        self.nxc_file = None
        self.nxc = None
        self.current_dir = os.getcwd()
        self.verbose = False
        self.workdir = self.current_dir
        self.debug = False
        self.prefix = "nxc"
        self.mode = {}
        self.flavour = {}
        self.flavour_name = None
        self.composition_name = None
        self.composition_flavour_prefix = None
        self.compose_info_file = None
        self.compose_info = None
        self.deployment_info = None
        self.deployment_info_b64 = ""
        self.ip_addresses = []
        self.host2ip_address = {}
        self.ssh = ""
        self.sudo = ""
        self.push_path = None
        self.platform = None
        self.artifact = False  # use artifact

    def init_workdir(self, env_name, env_id):
        with open(self.env_name_file, "w+") as fd:
            fd.write(env_name + "\n")
        if not os.path.exists(self.env_id_file):
            with open(self.env_id_file, "w+") as fd:
                fd.write(env_id + "\n")

    #    @property
    #    def state(self):
    #        if not hasattr(self, "_state"):
    #            self._state = State(self, state_file=self.state_file)
    #        return self._state
    #
    #    def update(self):
    #        self.state_file = op.join(self.envdir, "state.json")
    #        if "platform" in self.state:
    #            if self.state["platform"] == "Grid5000":
    #                self.platform = Grid5000Platform(self)

    def assert_valid_env(self):
        if not os.path.isdir(self.envdir):
            raise click.ClickException(
                "Missing nixos composition environment directory."
                " Run `nxc init` to create"
                " a new composition environment "
            )

    def log(self, msg, *args, **kwargs):
        """Logs a message to stdout."""
        if args:
            msg %= args
        kwargs.setdefault("file", sys.stdout)
        click.echo(msg, **kwargs)

    def wlog(self, msg, *args):
        """Logs a warning message to stderr."""
        self.log(click.style("Warning: %s" % msg, fg="yellow"), *args, file=sys.stderr)

    def elog(self, msg, *args):
        """Logs a error message to stderr."""
        self.log(click.style("Warning: %s" % msg, fg="red"), *args, file=sys.stderr)

    def glog(self, msg, *args):
        """Logs a green message."""
        self.log(click.style("%s" % msg, fg="green"), *args)

    def vlog(self, msg, *args):
        """Logs a message to stderr only if verbose is enabled."""
        if self.verbose:
            self.log(msg, *args, **{"file": sys.stderr})

    def handle_error(self, exception):
        exc_type, exc_value, tb = sys.exc_info()
        if not self.debug:
            sys.stderr.write(f"\nError: {exc_value}, exception {exception}\n")
            sys.exit(1)
        else:
            reraise(exc_type, exc_value, tb.tb_next)

    def elapsed_time(self):
        return time.time() - self.t0

    def show_elapsed_time(self):
        duration = "{:.2f}".format(self.elapsed_time())
        self.vlog("Elapsed Time: " + (click.style(duration, fg="green")) + " seconds")

    def load_nxc(self, f):
        self.nxc = json.load(f)
        if "platform" in self.nxc and self.nxc["platform"] == "Grid5000":
            self.platform = Grid5000Platform(self)


def make_pass_decorator(ensure=False):
    def decorator(f):
        @click.pass_context
        def new_func(*args, **kwargs):
            ctx = args[0]
            if ensure:
                obj = ctx.ensure_object(Context)
            else:
                obj = ctx.find_object(Context)
            try:
                return ctx.invoke(f, obj, *args[1:], **kwargs)
            except Exception as e:
                obj.handle_error(e)

        return update_wrapper(new_func, f)

    return decorator


class DeprecatedCmdDecorator(object):
    """This is a decorator which can be used to mark cmd as deprecated. It will
    result in a warning being emmitted when the command is invoked."""

    def __init__(self, message=""):
        if message:
            self.message = "%s." % message
        else:
            self.message = message

    def __call__(self, f):
        @click.pass_context
        def new_func(ctx, *args, **kwargs):
            msg = click.style(
                "warning: `%s` command is deprecated. %s"
                % (ctx.info_name, self.message),
                fg="yellow",
            )
            click.echo(msg)
            return ctx.invoke(f, *args, **kwargs)

        return update_wrapper(new_func, f)


class OnStartedDecorator(object):
    def __init__(self, callback):
        self.callback = callback
        self.exec_before = True

    def invoke_callback(self, ctx):
        if isinstance(self.callback, str):
            cmd = ctx.parent.command.get_command(ctx, self.callback)
            ctx.invoke(cmd)
        else:
            self.callback(ctx.obj)

    def __call__(self, f):
        @click.pass_context
        def new_func(ctx, *args, **kwargs):
            try:
                if self.exec_before:
                    self.invoke_callback(ctx)
                return ctx.invoke(f, *args, **kwargs)
            finally:
                if not self.exec_before:
                    self.invoke_callback(ctx)

        return update_wrapper(new_func, f)


class OnFinishedDecorator(OnStartedDecorator):
    def __init__(self, callback):
        super(on_finished, self).__init__(callback)
        self.exec_before = False


pass_context = make_pass_decorator(ensure=True)
on_started = OnStartedDecorator
on_finished = OnFinishedDecorator
