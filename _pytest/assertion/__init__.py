"""
support for presenting detailed information in failing assertions.
"""
import py
import os
import sys

from _pytest.config import hookimpl
from _pytest.monkeypatch import monkeypatch
from _pytest.assertion import util


def pytest_addoption(parser):
    group = parser.getgroup("debugconfig")
    group.addoption('--assert',
                    action="store",
                    dest="assertmode",
                    choices=("rewrite", "reinterp", "plain",),
                    default="rewrite",
                    metavar="MODE",
                    help="""control assertion debugging tools.  'plain'
                            performs no assertion debugging.  'reinterp'
                            reinterprets assert statements after they failed
                            to provide assertion expression information.
                            'rewrite' (the default) rewrites assert
                            statements in test modules on import to
                            provide assert expression information. """)
    group.addoption('--no-assert',
                    action="store_true",
                    default=False,
                    dest="noassert",
                    help="DEPRECATED equivalent to --assert=plain")
    group.addoption('--nomagic', '--no-magic',
                    action="store_true",
                    default=False,
                    help="DEPRECATED equivalent to --assert=plain")


class AssertionState:
    """State for the assertion plugin."""

    def __init__(self, config, mode):
        self.mode = mode
        self.trace = config.trace.root.get("assertion")


@hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    ns, ns_unknown_args = parser.parse_known_and_unknown_args(args)
    mode = ns.assertmode
    no_assert = ns.noassert
    no_magic = ns.nomagic
    if no_assert or no_magic:
        mode = "plain"
    if mode == "rewrite":
        try:
            import ast  # noqa
        except ImportError:
            mode = "reinterp"
        else:
            # Both Jython and CPython 2.6.0 have AST bugs that make the
            # assertion rewriting hook malfunction.
            if (sys.platform.startswith('java') or
                    sys.version_info[:3] == (2, 6, 0)):
                mode = "reinterp"

    early_config._assertstate = AssertionState(early_config, mode)
    warn_about_missing_assertion(mode, early_config.pluginmanager)

    if mode != "plain":
        _load_modules(mode)
        m = monkeypatch()
        early_config._cleanup.append(m.undo)
        m.setattr(py.builtin.builtins, 'AssertionError',
                  reinterpret.AssertionError)  # noqa

    hook = None
    if mode == "rewrite":
        hook = rewrite.AssertionRewritingHook()  # noqa
        hook.set_config(early_config)
        sys.meta_path.insert(0, hook)

    early_config._assertstate.hook = hook
    early_config._assertstate.trace("configured with mode set to %r" % (mode,))
    def undo():
        hook = early_config._assertstate.hook
        if hook is not None and hook in sys.meta_path:
            sys.meta_path.remove(hook)
    early_config.add_cleanup(undo)


def pytest_collection(session):
    # this hook is only called when test modules are collected
    # so for example not in the master process of pytest-xdist
    # (which does not collect test modules)
    hook = session.config._assertstate.hook
    if hook is not None:
        hook.set_session(session)


def _running_on_ci():
    """Check if we're currently running on a CI system."""
    env_vars = ['CI', 'BUILD_NUMBER']
    return any(var in os.environ for var in env_vars)


def pytest_runtest_setup(item):
    """Setup the pytest_assertrepr_compare hook

    The newinterpret and rewrite modules will use util._reprcompare if
    it exists to use custom reporting via the
    pytest_assertrepr_compare hook.  This sets up this custom
    comparison for the test.
    """
    def callbinrepr(op, left, right):
        """Call the pytest_assertrepr_compare hook and prepare the result

        This uses the first result from the hook and then ensures the
        following:
        * Overly verbose explanations are dropped unless -vv was used or
          running on a CI.
        * Embedded newlines are escaped to help util.format_explanation()
          later.
        * If the rewrite mode is used embedded %-characters are replaced
          to protect later % formatting.

        The result can be formatted by util.format_explanation() for
        pretty printing.
        """
        hook_result = item.ihook.pytest_assertrepr_compare(
            config=item.config, op=op, left=left, right=right)
        for new_expl in hook_result:
            if new_expl:
                if (sum(len(p) for p in new_expl[1:]) > 80*8 and
                        item.config.option.verbose < 2 and
                        not _running_on_ci()):
                    show_max = 10
                    truncated_lines = len(new_expl) - show_max
                    new_expl[show_max:] = [py.builtin._totext(
                        'Detailed information truncated (%d more lines)'
                        ', use "-vv" to show' % truncated_lines)]
                new_expl = [line.replace("\n", "\\n") for line in new_expl]
                res = py.builtin._totext("\n~").join(new_expl)
                if item.config.getvalue("assertmode") == "rewrite":
                    res = res.replace("%", "%%")
                return res
    util._reprcompare = callbinrepr


def pytest_runtest_teardown(item):
    util._reprcompare = None


def pytest_sessionfinish(session):
    hook = session.config._assertstate.hook
    if hook is not None:
        hook.session = None


def _load_modules(mode):
    """Lazily import assertion related code."""
    global rewrite, reinterpret
    from _pytest.assertion import reinterpret  # noqa
    if mode == "rewrite":
        from _pytest.assertion import rewrite  # noqa


def warn_about_missing_assertion(mode, pluginmanager):
    print('got here')
    try:
        assert False
    except AssertionError:
        pass
    else:
        if mode == "rewrite":
            specifically = ("assertions which are not in test modules "
                            "will be ignored")
        else:
            specifically = "failing tests may report as passing"

        # temporarily disable capture so we can print our warning
        capman = pluginmanager.getplugin('capturemanager')
        try:
            out, err = capman.suspendcapture()
            sys.stderr.write("WARNING: " + specifically +
                             " because assert statements are not executed "
                             "by the underlying Python interpreter "
                             "(are you using python -O?)\n")
        finally:
            capman.resumecapture()
            sys.stdout.write(out)
            sys.stderr.write(err)


# Expose this plugin's implementation for the pytest_assertrepr_compare hook
pytest_assertrepr_compare = util.assertrepr_compare
