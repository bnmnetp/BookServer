# *************************
# |docname| - Runestone API
# *************************
# This module implements the API that the Runestone Components use to get results from assessment components
#
# *     multiple choice
# *     fill in the blank
# *     parsons problems
# *     drag and dorp
# *     clickable area
#
# Imports
# =======
# These are listed in the order prescribed by `PEP 8`_.
#
# Standard library
# ----------------

# Third-party imports
# -------------------
from fastapi import APIRouter, Request

# Local application imports
# -------------------------
from ..applogger import rslogger
from ..crud import fetch_last_answer_table_entry
from ..schemas import AssessmentRequest
from ..session import is_instructor

# Routing
# =======
# See `APIRouter config` for an explanation of this approach.
router = APIRouter(
    prefix="/assessment",
    tags=["assessment"],
)


# getAssessResults
# ----------------
@router.post("/results")
async def get_assessment_results(
    request_data: AssessmentRequest,
    request: Request,
):

    # if the user is not logged in an HTTP 401 will be returned.
    # Otherwise if the user is an instructor then use the provided
    # sid (it could be any student in the class) If none is provided then
    # use the user objects username
    if await is_instructor(request):
        if not request_data.sid:
            request_data.sid = request.state.user.username
    else:
        request_data.sid = request.state.user.username

    row = await fetch_last_answer_table_entry(request_data)
    if not row:
        return ""  # server doesn't have it so we load from local storage instead

    # :index:`todo``: **port the serverside grading** code::
    #
    #   do_server_feedback, feedback = is_server_feedback(div_id, course)
    #   if do_server_feedback:
    #       correct, res_update = fitb_feedback(rows.answer, feedback)
    #       res.update(res_update)
    rslogger.debug(f"Returning {row}")
    return row
