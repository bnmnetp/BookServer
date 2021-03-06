# ***************************************
# |docname| - pytest fixtures for testing
# ***************************************
#
# ``conftest.py`` is the standard file for defining **fixtures**
# for `pytest <https://docs.pytest.org/en/stable/fixture.html>`_.
# One job of a fixture is to arrange and set up the environment
# for the actual test.
# It may seem a bit mysterious to newcomers that you define
# fixtures in here and use them in your various ``xxx_test.py`` files
# especially because you do not need to import the fixtures they just
# magically show up.  Bizarrely fixtures are called into action on
# behalf of a test by adding them as a parameter to that test.
#
#
# Imports
# =======
# These are listed in the order prescribed by `PEP 8
# <http://www.python.org/dev/peps/pep-0008/#imports>`_.
#
# Standard library
# ----------------
import os
import subprocess
import sys
from threading import Thread
from shutil import rmtree, copytree
from urllib.error import URLError
from urllib.request import urlopen

# Third-party imports
# -------------------
from fastapi.testclient import TestClient
from _pytest.monkeypatch import MonkeyPatch
import pytest
from pyvirtualdisplay import Display

# Since ``selenium_driver`` is a parameter to a function (which is a fixture), flake8 sees it as unused. However, pytest understands this as a request for the ``selenium_driver`` fixture and needs it.
from runestone.shared_conftest import _SeleniumUtils, selenium_driver  # noqa: F401
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from sqlalchemy.sql import text

# Local imports
# -------------
from bookserver.config import DatabaseType, settings
from bookserver.db import async_session, engine
from bookserver.crud import create_user, create_course
from bookserver.main import app
from bookserver.models import AuthUserValidator, CoursesValidator
from .ci_utils import xqt, pushd


# Pytest setup
# ============
# Add `command-line options <http://doc.pytest.org/en/latest/example/parametrize.html#generating-parameters-combinations-depending-on-command-line>`_.
def pytest_addoption(parser):
    # Per the `API reference <http://doc.pytest.org/en/latest/reference.html#_pytest.hookspec.pytest_addoption>`_,
    # options are argparse style.
    parser.addoption(
        "--skipdbinit",
        action="store_true",
        help="Skip initialization of the test database.",
    )


# Output a coverage report when testing is done. See https://docs.pytest.org/en/latest/reference.html#_pytest.hookspec.pytest_terminal_summary.
def pytest_terminal_summary(terminalreporter):
    try:
        cp = xqt(
            "{} -m coverage report".format(sys.executable),
            # Capture the output from the report.
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except subprocess.CalledProcessError as e:
        res = "Error in coverage report.\n{}".format(e.stdout + e.stderr)
    else:
        res = cp.stdout + cp.stderr
    terminalreporter.write_line(res)


# Server prep and run
# ===================
@pytest.fixture(scope="session")
def bookserver_address():
    return "http://localhost:8080"


# This fixture starts and shuts down the web2py server.
#
# Execute this `fixture <https://docs.pytest.org/en/latest/fixture.html>`_ once per `session <https://docs.pytest.org/en/latest/fixture.html#scope-sharing-a-fixture-instance-across-tests-in-a-class-module-or-session>`_.
@pytest.fixture(scope="session")
def run_bookserver(bookserver_address, pytestconfig):
    if pytestconfig.getoption("skipdbinit"):
        print("Skipping DB initialization.")
    else:
        # Copy the test book to the books directory.
        test_book_path = f"{settings.book_path}/test_course_1"
        rmtree(test_book_path, ignore_errors=True)
        # Sometimes this fails for no good reason on Windows. Retry.
        for retry in range(100):
            try:
                copytree(
                    f"{settings.web2py_path}/tests/test_course_1",
                    test_book_path,
                )
                break
            except OSError:
                if retry == 99:
                    raise

        # Start the app to initialize the database.
        with TestClient(app):
            pass

        # Build the test book to add in db fields needed.
        with pushd(test_book_path), MonkeyPatch().context() as m:
            # The runestone build process only looks at ``DBURL``.
            sync_dburl = settings.database_url.replace("+asyncpg", "").replace(
                "+aiosqlite", ""
            )
            m.setenv("WEB2PY_CONFIG", "test")
            m.setenv("TEST_DBURL", sync_dburl)
            xqt(
                "{} -m runestone build --all".format(sys.executable),
                "{} -m runestone deploy".format(sys.executable),
            )

    xqt("{} -m coverage erase".format(sys.executable))

    # For debug:
    #
    # #.    Uncomment the next three lines.
    # #.    Set ``WEB2PY_CONFIG`` to ``test``; all the other usual Runestone environment variables must also be set.
    # #.    Run ``python -m celery --app=scheduled_builder worker --pool=gevent --concurrency=4 --loglevel=info`` from ``applications/runestone/modules`` to use the scheduler. I'm assuming the redis server (which the tests needs regardless of debug) is also running.
    # #.    Run a test (in a separate window). When the debugger stops at the lines below:
    #
    #       #.  Run web2py manually to see all debug messages. Use a command line like ``python web2py.py -a pass``.
    #       #.  After web2py is started, type "c" then enter to continue the debugger and actually run the tests.
    ##import pdb; pdb.set_trace()
    ##yield
    ##return

    # Start the bookserver and the (eventually) the scheduler. TODO: if an exception occurs, then this process isn't killed.
    book_server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--append",
            "--source=bookserver",
            "-m",
            "bookserver",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Produce text (not binary) output for nice output in ``echo()`` below.
        universal_newlines=True,
    )
    # Run Celery. Per https://github.com/celery/celery/issues/3422, it sounds like celery doesn't support coverage, so omit it.
    if False:
        # TODO: implement server-side grading. Until then, not needed.
        celery_process = subprocess.Popen(  # noqa: F841
            [
                sys.executable,
                "-m",
                "celery",
                "--app=scheduled_builder",
                "worker",
                "--pool=gevent",
                "--concurrency=4",
                "--loglevel=info",
            ],
            # Celery must be run in the ``modules`` directory, where the worker is defined.
            # cwd="{}/modules".format(rs_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Produce text (not binary) output for nice output in ``echo()`` below.
            universal_newlines=True,
        )

    # Start a thread to read bookserver output and echo it.
    def echo(popen_obj, description_str):
        stdout, stderr = popen_obj.communicate()
        print("\n" "{} stdout\n" "--------------------\n".format(description_str))
        print(stdout)
        print("\n" "{} stderr\n" "--------------------\n".format(description_str))
        print(stderr)

    echo_threads = [
        Thread(target=echo, args=(book_server_process, "book server")),
        ##Thread(target=echo, args=(celery_process, "celery process")),
    ]
    for echo_thread in echo_threads:
        echo_thread.start()

    print("Waiting for the webserver to come up...")
    for tries in range(10):
        try:
            urlopen(bookserver_address, timeout=1)
            break
        except URLError as e:
            print(e)
            # Wait for the server to come up.
            pass
    else:
        print("Failure, server not up.")
        ##assert False, "Server not up."
    print("done.\n")

    # After this comes the `teardown code <https://docs.pytest.org/en/latest/fixture.html#fixture-finalization-executing-teardown-code>`_.
    yield

    # Terminate the server and schedulers to give web2py time to shut down gracefully.
    book_server_process.terminate()
    ##celery_process.terminate()
    for echo_thread in echo_threads:
        echo_thread.join()


# Database
# ========
#
# .. _bookserver_session:
#
# bookserver_session
# ------------------
# This fixture provides access to a clean instance of the Runestone database.
@pytest.fixture
async def bookserver_session(run_bookserver):
    # **Clean the database state before a test**
    ##------------------------------------------
    # This list was generated by running the following query, taken from
    # https://dba.stackexchange.com/a/173117. Note that the query excludes
    # specific tables, which the ``runestone build`` populates and which
    # should not be modified otherwise. One method to identify these tables
    # which should not be truncated is to run ``pg_dump --data-only
    # $TEST_DBURL > out.sql`` on a clean database, then inspect the output to
    # see which tables have data. It also excludes all the scheduler tables,
    # since truncating these tables makes the process take a lot longer.
    #
    # The query is:
    ## SELECT input_table_name AS truncate_query FROM(SELECT table_name AS input_table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_name NOT IN ('questions', 'source_code', 'chapters', 'sub_chapters', 'scheduler_run', 'scheduler_task', 'scheduler_task_deps', 'scheduler_worker') AND table_schema NOT LIKE 'pg_toast%') AS information order by input_table_name;
    tables_to_delete = (
        """
        assignment_questions
        assignments
        auth_cas
        auth_event
        auth_group
        auth_membership
        auth_permission
        auth_user
        clickablearea_answers
        code
        codelens_answers
        course_attributes
        course_instructor
        course_practice
        courses
        dragndrop_answers
        fitb_answers
        grades
        lp_answers
        invoice_request
        lti_keys
        mchoice_answers
        parsons_answers
        payments
        practice_grades
        question_grades
        question_tags
        shortanswer_answers
        sub_chapter_taught
        tags
        timed_exam
        useinfo
        user_biography
        user_chapter_progress
        user_courses
        user_state
        user_sub_chapter_progress
        user_topic_practice
        user_topic_practice_completion
        user_topic_practice_feedback
        user_topic_practice_log
        user_topic_practice_survey
        web2py_session_runestone
        """
    ).split()

    async with engine.begin() as conn:
        if settings.database_type == DatabaseType.PostgreSQL:
            tables = ", ".join(tables_to_delete)
            await conn.execute(text(f"TRUNCATE {tables} CASCADE;"))
        else:
            for table in tables_to_delete:
                print(table)
                try:
                    await conn.execute(text(f"DELETE FROM {table};"))
                except Exception as e:
                    print(e)

    # The database is clean. Proceed with the test.
    yield async_session


# User management
# ---------------
@pytest.fixture
def create_test_course(bookserver_session):
    async def _create_test_course(**kwargs):
        course = CoursesValidator(**kwargs)
        await create_course(course)
        return course

    return _create_test_course


@pytest.fixture
async def test_course_1(create_test_course):
    return await create_test_course(
        course_name="test_child_course_1",
        term_start_date="2000-01-01",
        login_required=True,
        base_course="test_course_1",
        student_price=None,
    )


# A class to hold a user plus the class the user is in.
class TestAuthUserValidator(AuthUserValidator):
    course: CoursesValidator


@pytest.fixture
def create_test_user(bookserver_session):
    async def _create_test_user(**kwargs):
        # TODO: Add this user to the provided course.
        course = kwargs.pop("course")
        user = AuthUserValidator(**kwargs)
        assert await create_user(user)
        return TestAuthUserValidator(course=course, **kwargs)

    return _create_test_user


# Provide a way to get a prebuilt test user.
@pytest.fixture
async def test_user_1(create_test_user, test_course_1):
    return await create_test_user(
        username="test_user_1", password="password_1", course=test_course_1
    )


# Selenium
# ========
# Provide access to Runestone through a web browser using Selenium. There's a lot of shared code between these tests and the Runestone Component tests using Selenium; see :ref:`shared_conftest.py` for details.
#
# Create an instance of Selenium once per testing session.
@pytest.fixture(scope="session")
def selenium_driver_session():
    # Start a virtual display for Linux.
    is_linux = sys.platform.startswith("linux")
    if is_linux:
        display = Display(visible=0, size=(1280, 1024))
        display.start()
    else:
        display = None

    # Start up the Selenium driver.
    options = Options()
    options.add_argument("--window-size=1200,800")
    # When run as root, Chrome complains ``Running as root without --no-sandbox is not supported. See https://crbug.com/638180.`` Here's a `crude check for being root <https://stackoverflow.com/a/52621917>`_.
    if is_linux and os.geteuid() == 0:
        options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=options)

    yield driver

    # Shut everything down.
    driver.close()
    driver.quit()
    if display:
        display.stop()


# Provide additional server methods for Selenium.
class _SeleniumServerUtils(_SeleniumUtils):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None

    def login(
        self,
        # A ``_TestUser`` instance.
        test_user,
    ):

        self.get("auth/login")
        self.driver.find_element_by_id("loginuser").send_keys(test_user.username)
        self.driver.find_element_by_id("loginpw").send_keys(test_user.password)
        self.driver.find_element_by_id("login_button").click()
        self.user = test_user

    def logout(self):
        # TODO: No such endpoint.
        return
        self.get("auth/logout")
        # For some strange reason, the server occasionally doesn't put the "Logged out" message on a logout. ???
        try:
            self.wait.until(
                EC.text_to_be_present_in_element(
                    (By.CSS_SELECTOR, "div.flash"), "Logged out"
                )
            )
        except TimeoutException:
            # Assume that visiting the logout URL then waiting for a timeout will ensure the logout worked, even if the message can't be found.
            pass
        self.user = None

    def get_book_url(self, url):
        return self.get(f"books/published/test_course_1/{url}")


# Present ``_SeleniumServerUtils`` as a fixture.
@pytest.fixture
def selenium_utils(selenium_driver, bookserver_address):  # noqa: F811
    return _SeleniumServerUtils(selenium_driver, bookserver_address)


# A fixture to login to the test_user_1 account using Selenium before testing, then logout when the tests complete.
@pytest.fixture
def selenium_utils_user(selenium_utils, test_user_1):
    selenium_utils.login(test_user_1)
    yield selenium_utils
    selenium_utils.logout()
