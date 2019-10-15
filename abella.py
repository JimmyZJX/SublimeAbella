
# builtin modules
import collections
import os.path
import re
import string
import subprocess
from subprocess import Popen, PIPE
import threading
import time

# sublime
import sublime
import sublime_plugin

ABELLA_BIN = "abella.exe"

workers = dict()
known_views = dict()
# messages
StopMessage = object()
NextMessage = object()
UndoMessage = object()
GotoMessage = object()
ShowMessage = collections.namedtuple("ShowMessage", ["thm"])
# EvalMessage = collections.namedtuple("EvalMessage", ["pos"])

class WorkerContinueException(Exception):
    pass

abellaWindow = None
def getAbellaWindow():
    global abellaWindow
    if abellaWindow and abellaWindow.id() in map(lambda w: w.id(), sublime.windows()):
        return abellaWindow
    else:
        # try to find existing window
        for window in sublime.windows():
            if len(window.views()) == 0 or window.views()[0].name().startswith("*** Abella"):
                abellaWindow = window
                return abellaWindow
        sublime.run_command("new_window")
        abellaWindow = sublime.active_window()
        return abellaWindow

REAbellaNextDot = re.compile(r"(%.*?\n|/\*.*?\*/|/[^*]|[^%/.])*\.\s", re.DOTALL)

class AbellaWorker(threading.Thread):

    def __init__(self, view, response_view=None):
        super().__init__(daemon=True)

        self.eventReq = threading.Event()

        self.view = view
        view.lastShowThm = ""
        if not hasattr(view, 'lastProven'):
            view.lastProven = view.size() # the last "Proof completed."
        print("view.lastProven = ", view.lastProven)

        self.working_dir = None
        f = self.view.file_name()
        if f is not None:
            self.working_dir = os.path.dirname(f)

        self.view = view
        if response_view:
            self.response_view = known_views[response_view]
            del known_views[response_view]
        else:
            self.response_view = getAbellaWindow().new_file() # self.view.window().new_file()
            self._init_response_view()

        self._init_popen()

    def _init_popen(self):
        self.p = Popen(ABELLA_BIN, universal_newlines=True,
                       stdin=PIPE, stdout=PIPE, cwd=self.working_dir,
                       shell=True, bufsize=0, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

        self.AbellaUndo = False
        self.pos = 0
        self.undoStack = AbellaUndo()
        self.communicate("")
        self.communicate("Set undo off.", is_crucial=True)

    def _init_response_view(self):
        self.response_view.set_syntax_file("Packages/SublimeAbella/Abella.tmLanguage")
        self.response_view.set_scratch(True)
        self.response_view.set_read_only(True)
        name = self.view.name() or os.path.basename(self.view.file_name() or "")
        title = "*** Abella for {} ***".format(name) if name else "*** Abella ***"
        self.response_view.set_name(title)

        # window = self.view.window()
        # ngroups = window.num_groups()
        # if ngroups == 1:
        #     window.run_command("new_pane")
        # else:
        #     group = window.num_groups() - 1
        #     if window.get_view_index(self.view)[1] == group:
        #         group -= 1
        #     window.set_view_index(self.response_view, group, 0)
        # window.focus_view(self.view)

    def _do_stop(self):
        self.p.communicate(".\nQuit.\n")
        self.send_req(StopMessage)

    def _on_stop(self):
        print("Killing Abella...")
        try:
            self.p.terminate()
        except Exception as e:
            print(e)
        self.view.erase_regions("Abella")

    def send_req(self, req):
        self.request = req
        self.eventReq.set();

    def get_req(self):
        self.eventReq.wait()
        self.eventReq.clear()
        return self.request

    def run(self):
        while True:
            try:
                req = self.get_req()
                # print("worker {} got message: {}".format(self, req))
                if req is StopMessage:
                    print("worker {} got stop message".format(self))
                    self._on_stop()
                    return
                elif req is NextMessage:
                    self.next()
                elif req is UndoMessage:
                    self.undo()
                elif req is GotoMessage:
                    self.goto()
                elif isinstance(req, ShowMessage):
                    self.show(req.thm)
                # elif req is CheckForModificationMessage:
                #     print("worker {} is checking for modifications...")
                #     self.check_for_modifications()
                else:
                    print("unknown message: ", req)
            except WorkerContinueException:
                print("WorkerContinueException: ", self)
                self.goto()

    def nextPos(self, text=None):
        text = text or self.view.substr(sublime.Region(0, self.view.size()))
        # print("nextPos.text = " + text)
        m = REAbellaNextDot.match(text, self.pos)
        if m:
            return m.end()
        else:
            return None

    def next(self, updateCursor=True, nextPos=None, updateRegion=False):
        nextPos = nextPos or self.nextPos()
        # print("next.nextPos = " + str(nextPos))
        if nextPos:
            # next_fullstop_region = sublime.Region(self.pos, nextPos)
            # print("region: " + self.view.substr(next_fullstop_region))
            next_fullstop = nextPos - 1
            next_str = self.view.substr(sublime.Region(self.pos, next_fullstop))
            (out, err) = self.communicate(next_str)
            if not err:
                self.pos = next_fullstop

            if out.startswith("Proof completed."):
                self.view.lastProven = self.pos
                updateRegion = True
                # print("Yey! lastProven -> ", self.view.lastProven)

            self.commit(out, updateCursor=updateCursor, updateRegion=updateRegion)

            return not err
        else:
            print("Abella - Cannot find match...")
            return False

    def undo(self, updateCursor=True):
        # clean the undoStack
        while self.undoStack.top() == ("", ""):
            self.undoStack.pop()
            self.do_communicate("undo.", is_text=False)

        out = "Undo not available!"
        if not self.undoStack.top()[1].startswith("#") and self.pos > 0:
            (out, err) = self.communicate("undo.", is_text=False)
            if not err:
                self.pos -= len(self.undoStack.pop()[0])

                # Get last non-empty element
                lastOutp = ""
                for i in range(len(self.undoStack.stack)):
                    if self.undoStack.stack[-i - 1] != ("", ""):
                        lastOutp = self.undoStack.stack[-i - 1][1]
                        break
                # print("=== Output ===\n" + lastOutp)
                self.commit(out if "\n" in out else lastOutp, updateCursor=updateCursor)
                return True

        self.commit(out, "Undo Failed...", updateCursor=updateCursor)
        return False

    def enableAbellaUndo(self):
        self.communicate("Set undo on.", is_text=False)
        self.AbellaUndo = True
        self.undoStack.push("#undo_on", "#undo_on")

    def goto(self):
        lastProven = self.view.lastProven

        cursor = self.view.sel()[0].begin()
        self.buffered_commit = self.response_view.substr(sublime.Region(0, self.response_view.size()))
        if cursor > self.pos:
            viewText = self.view.substr(sublime.Region(0, self.view.size()))
            nextPos = self.nextPos(text=viewText)
            while nextPos and nextPos - 1 <= cursor and self.next(updateCursor=False, nextPos=nextPos):
                nextPos = self.nextPos(text=viewText)
                # print("nextPos = " + str(nextPos))
                if self.AbellaUndo == False and nextPos > lastProven:
                    print("nextPos({}) > lastProven({})".format(nextPos, lastProven))
                    self.enableAbellaUndo()
        else:
            while cursor < self.pos and self.undo(updateCursor=False):
                pass
        if self.AbellaUndo == False:
            print("Just set undo on back again")
            self.enableAbellaUndo()
        self.commit_buffer()

    def show(self, thm):
        if self.pos > 0:
            (out, err) = self.communicate("Show " + thm + ".", is_text=False, is_show=True)
            spanel = self.view.window().find_output_panel('show_thm')
            if not spanel:
                print("Cannot find output panel!")
                return False
            if not err:
                msg = out
                # Beautify message
                if msg.endswith(" < "):
                    (msgBody, msgHead) = ("\n" + msg).rsplit("\n", 1)
                    if len(msgBody) > 0 : msgBody = msgBody[1:]
                spanel.run_command("edit_panel", {'text': msgBody})
                return True
            else:
                spanel.run_command("edit_panel", {'text': "Show Failed: " + err})
                return False

    def communicate(self, str_input, is_text=True, is_show=False, is_crucial=False):
        # clean the undoStack
        while self.undoStack.top() == ("", ""):
            self.undoStack.pop()
            self.do_communicate("undo.", is_text=False)

        (out, err) = self.do_communicate(str_input, is_text, is_show)
        if self.AbellaUndo == False and err is not None:
            if is_crucial:
                sublime.error_message("Crucial Error: <" + err + ">")
                try:
                    self.p.terminate()
                except Exception as e:
                    print(e)
                raise Exception("Crucial Error: " + err)
            print("Something bad <" + err + ">: Abella should quit now")
            try:
                self.p.terminate()
            except Exception as e:
                print(e)
            self._init_popen() # resets all the states
            raise WorkerContinueException() # will call goto
        return (out, err)

    def do_communicate(self, str_input, is_text=True, is_show=False):
        if not is_show:
            self.response_view.run_command("abella_start_working")
        # print("communicate: " + str_input)
        self.p.stdin.write(str_input + "\n")
        self.p.stdin.flush()
        output = ""
        while not output.endswith(" < "):
            outChar = self.p.stdout.read(1)
            if outChar == '':
                self.p.poll()
                print("output ends?! " + output + "; pid: " + str(self.p.pid) + "; return code: " + str(self.p.returncode))
                break
            output += outChar
        match = re.search(r"(Error: .*)|( error\.)", output)
        if match:
            return (output, match.group(0))
        else:
            if is_text: self.undoStack.push(str_input, output)
            if is_show: self.undoStack.push("", "")
            return (output, None)

    def commit(self, msg, head=None, updateCursor=True, updateRegion=False):
        if updateRegion or updateCursor:
            self.view.add_regions("Abella", [sublime.Region(0, self.pos)], "region.greenish meta.coq.proven")

        if updateCursor:
            self.view.run_command("update_cursor", { "pos": self.pos }) # TODO

            self.response_update_output(msg, head=head)
            if self.response_view.window().id() != self.view.window().id():
                self.response_view.window().focus_view(self.response_view)
        else:
            self.buffered_commit = msg

    def beautify_msg(self, msg, head):
        # Beautify message
        if msg.endswith(" < "):
            (msgBody, msgHead) = ("\n" + msg).rsplit("\n", 1)
            if len(msgBody) > 0 : msgBody = msgBody[1:]
            msgHead = msgHead[:-3]
            msgHead = "" if msgHead == "Abella" else "Proving Theorem " + msgHead

            if head: msgHead = head
            msg = msgHead + "\n======\n" + msgBody
        return msg

    def response_update_output(self, msg, head=None):
        self.response_view.run_command("abella_update_output_buffer", {"text": self.beautify_msg(msg, head)})

    def commit_buffer(self):
        self.view.add_regions("Abella", [sublime.Region(0, self.pos)], "region.greenish meta.coq.proven")
        self.response_update_output(self.buffered_commit)
        self.buffered_commit = ""
        if self.response_view.window().id() != self.view.window().id():
            self.response_view.window().focus_view(self.response_view)

class AbellaUndo(object):
    def __init__(self):
        self.stack = [("#init#","#init#")]
        self.text = ""

    def push(self, text, msg):
        self.text += text
        self.stack.append((text, msg))

    def pop(self):
        (text, msg) = self.stack.pop()
        self.text = self.text[:-len(text)]
        return (text, msg)

    def top(self):
        (text, msg) = self.stack[-1]
        self.text = self.text[:-len(text)]
        return (text, msg)

class AbellaCommand(sublime_plugin.TextCommand):
    def run(self, edit, response_view=None):
        # if "coq" not in self.view.scope_name(0):
        #     print("not inside a coq buffer")
        #     return
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        print("AbellaCommand {} got worker {}".format(worker_key, worker))
        if not worker:
            worker = AbellaWorker(self.view, response_view)
            workers[worker_key] = worker
            # print("AbellaCommand worker created {}".format(worker,))
            worker.start()
            print("spawned worker {} for view {}".format(worker, worker_key))
        worker.send_req(GotoMessage)

class AbellaNextCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            worker.send_req(NextMessage)
        else:
            print("No worker found for view {}".format(worker_key))

class AbellaUndoCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            worker.send_req(UndoMessage)
        else:
            print("No worker found for view {}".format(worker_key))

class AbellaGotoCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            worker.send_req(GotoMessage)
        else:
            print("No worker found for view {}".format(worker_key))

class AbellaReloadCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            response_view = worker.response_view
            known_views[response_view.id()] = response_view
            print(response_view)
            self.view.run_command("abella_kill", {"close_response": False})
            if worker.is_alive():
            	print("worker didn't die!")
            	return
            self.view.run_command("abella", {"response_view": response_view.id()})
        else:
            self.view.run_command("abella_kill")
            self.view.run_command("abella")

class AbellaKillCommand(sublime_plugin.TextCommand):
    def run(self, edit, close_response=True):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            del workers[worker_key]
            worker._do_stop()
            worker.join(5)
            if not worker.is_alive():
                print("killed {}".format(worker))
            else:
                print("worker didn't die!")

            if close_response:
                # clean up the response view if it still exists
                response_view = worker.response_view
                window = response_view.window()
                if window is not None:
                    window.focus_view(response_view)
                    window.run_command("close")
        else:
            print("no worker to kill")
            self.view.erase_regions("Abella")

viewPort = dict()

class AbellaUpdateOutputBufferCommand(sublime_plugin.TextCommand):
    def run(self, edit, text=""):
        viewPort[self.view.id()] = self.view.viewport_position()
        # print("viewport = " + str(viewPort[self.view.id()]))
        self.view.set_read_only(False)
        # self.view.replace(edit, sublime.Region(0, self.view.size()), text)
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.insert(edit, 0, text)
        # self.view.show(0)
        self.view.set_name(self.view.name().replace("===", "***"))
        self.view.set_read_only(True)
        # self.view.set_viewport_position(viewport)
        # print("after = " + str(self.view.viewport_position()))

class UpdateCursorCommand(sublime_plugin.TextCommand):
    def run(self, edit, pos=0):
        self.view.sel().subtract(sublime.Region(0, pos - 1))
        if len(self.view.sel()) == 0:
            self.view.sel().add(sublime.Region(pos, pos))

class AbellaStartWorkingCommand(sublime_plugin.TextCommand):
    def run(self, edit, text=""):
        self.view.set_read_only(False)
        self.view.replace(edit, self.view.line(0), "Abella working...")
        self.view.set_read_only(True)

class AbellaViewEventListener(sublime_plugin.EventListener):

    # def on_clone(self, view):
    #     pass # TODO: what happens when coq response views are duplicated?

    # def on_modified(self, view):
    #     worker_key = view.id()
    #     worker = coq_threads.get(worker_key, None)
    #     if worker:
    #         worker.send_req(CheckForModificationMessage)

    def on_modified(self, view):
        if viewPort.get(view.id(), None):
            # print("after = " + str(viewPort[view.id()]))
            view.set_viewport_position(viewPort[view.id()], False)
        viewPort[view.id()] = None

    def on_close(self, view):
        for view_id, worker in list(workers.items()):
            if worker.response_view.id() == view.id():
                worker.view.run_command("abella_kill")

class EditPanelCommand(sublime_plugin.TextCommand):
    def run(self, edit, text=''):
        self.view.insert(edit, self.view.size(), text)

class AbellaShowCommand(sublime_plugin.TextCommand):
    def run(self, edit, thm=None, view_id=-1):
        if thm and view_id >= 0:
            worker_key = view_id
            worker = workers.get(worker_key, None)
            if worker:
                worker.send_req(ShowMessage(thm=thm))
            else:
                print("No worker found for view {}".format(worker_key))

class AbellaListShowCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        self.view_id = self.view.id()
        self.list_items = []

        for v in self.view.window().views():
            if v.file_name() and v.file_name().endswith(".thm"):
                self.list_items += [name for (r, name) in v.symbols()]

        if self.view.substr(self.view.sel()[0]) in self.list_items:
            self.show_thm(self.view.substr(self.view.sel()[0]))
        else:
            applying_thm = self.get_applying_thm()
            if applying_thm in self.list_items and \
                    (self.view.lastShowThm != applying_thm or \
                     self.view.window().find_output_panel('show_thm').window() == None):
                self.show_thm(applying_thm)
            else:
                sublime.active_window().show_quick_panel(self.list_items, self._on_select)

    def get_applying_thm(self):
        txt = self.view.substr(sublime.Region(0, self.view.sel()[0].end()))
        lastDot = txt.rfind('.')
        if lastDot < 0: return ""
        # print("dot = " + txt[lastDot:])
        match = re.search(r"(applys?|backchain) +(\w+)", txt[lastDot:])
        if match:
            # print("match = " + match.group(2))
            return match.group(2)
        return ""

    def _on_select(self, idx):
        if idx > -1:
            self.show_thm(self.list_items[idx])
            # sublime.message_dialog(thm)

    def show_thm(self, thm):
        self.panel = self.view.window().create_output_panel('show_thm')
        self.view.window().run_command('show_panel', { 'panel': 'output.show_thm' })
        self.view.lastShowThm = thm
        self.panel.run_command('abella_show', { 'thm': thm, 'view_id': self.view_id });

