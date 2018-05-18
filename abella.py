
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

class AbellaWorker(threading.Thread):

    def __init__(self, view, response_view=None):
        super().__init__(daemon=True)
        self.pos = 0

        self.lock = threading.Lock()
        self.lock.acquire()

        self.view = view
        working_dir = None
        f = self.view.file_name()
        if f is not None:
            working_dir = os.path.dirname(f)

        self.p = Popen(ABELLA_BIN, universal_newlines=True,
                       stdin=PIPE, stdout=PIPE, cwd=working_dir,
                       shell=True, bufsize=0)
        self.view = view
        if response_view:
            self.response_view = known_views[response_view]
            del known_views[response_view]
        else:
            self.response_view = getAbellaWindow().new_file() # self.view.window().new_file()
            self._init_response_view()

        self.undoStack = AbellaUndo()
        self.communicate("")

    def _init_response_view(self):
        self.response_view.set_syntax_file("Packages/Abella/Abella.tmLanguage")
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
        self.p.terminate()
        self.view.erase_regions("Abella")

    def send_req(self, req):
        self.request = req
        if not self.lock.acquire(blocking=False):
            self.lock.release()

    def get_req(self):
        self.lock.acquire()
        return self.request

    def run(self):
        while True:
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
                print("unknown message: {}".format(req))

    def nextPos(self):
        text = self.view.substr(sublime.Region(self.pos, self.view.size()))
        # print("nextPos.text = " + text)
        m = re.match(r"((%.*\n)|(/\*.*\*+/)|(/[^*%]|[^%.]))*\.\s", text)
        if m:
            return (self.pos + m.end())
        else:
            return None

    def next(self, updateCursor=True):
        nextPos = self.nextPos()
        # print("next.nextPos = " + str(nextPos))
        if nextPos:
            next_fullstop_region = sublime.Region(self.pos, nextPos)
            # print("region: " + self.view.substr(next_fullstop_region))
            next_fullstop = next_fullstop_region.b - 1
            next_str = self.view.substr(sublime.Region(self.pos, next_fullstop))
            (out, err) = self.communicate(next_str)
            if not err:
                self.pos = next_fullstop
            self.commit(out, updateCursor=updateCursor)
            return not err
        else:
            print("Abella - Cannot find match...")
            return False

    def undo(self, updateCursor=True):
        if self.pos > 0:
            while self.undoStack.top() == ("", ""):
                self.communicate("undo.", is_text=False)
                self.undoStack.pop()
            (out, err) = self.communicate("undo.", is_text=False)
            if not err:
                self.pos -= len(self.undoStack.pop()[0])
                self.commit(out, updateCursor=updateCursor)
                return True
            else:
                self.commit(out, "Undo Failed...", updateCursor=updateCursor)
                return False

    def goto(self):
        cursor = self.view.sel()[0].begin()
        if cursor > self.pos:
            nextPos = self.nextPos()
            while nextPos and nextPos - 1 <= cursor and self.next(updateCursor=False):
                nextPos = self.nextPos()
                # print("nextPos = " + str(nextPos))
        else:
            while cursor < self.pos and self.undo(updateCursor=False):
                pass
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

    def communicate(self, str_input, is_text=True, is_show=False):
        if not is_show:
            self.response_view.run_command("abella_start_working")
        self.p.stdin.write(str_input + "\n")
        self.p.stdin.flush()
        output = ""
        while not output.endswith(" < "):
            output += self.p.stdout.read(1)
        match = re.search(r"(Error: .*)|( error\.)", output)
        if match:
            return (output, match.group(0))
        else:
            if is_text: self.undoStack.push(str_input, output)
            if is_show: self.undoStack.push("", "")
            return (output, None)

    def commit(self, msg, head=None, updateCursor=True):
        self.view.add_regions("Abella", [sublime.Region(self.pos - 1, self.pos)], "meta.coq.proven")
        if updateCursor:
            self.view.sel().subtract(sublime.Region(0, self.pos - 1))
            if len(self.view.sel()) == 0:
                self.view.sel().add(sublime.Region(self.pos, self.pos))
        # Beautify message
        if msg.endswith(" < "):
            (msgBody, msgHead) = ("\n" + msg).rsplit("\n", 1)
            if len(msgBody) > 0 : msgBody = msgBody[1:]
            msgHead = msgHead[:-3]
            msgHead = "" if msgHead == "Abella" else "Proving Theorem " + msgHead

            if head: msgHead = head
            msg = msgHead + "\n======\n" + msgBody
        if updateCursor:
            self.response_view.run_command("abella_update_output_buffer", {"text": msg})
            if self.response_view.window().id() != self.view.window().id():
                self.response_view.window().focus_view(self.response_view)
        else:
            self.buffered_commit = msg

    def commit_buffer(self):
        self.response_view.run_command("abella_update_output_buffer", {"text": self.buffered_commit})
        self.buffered_commit = ""
        if self.response_view.window().id() != self.view.window().id():
            self.response_view.window().focus_view(self.response_view)

class AbellaUndo(object):
    def __init__(self):
        self.stack = []
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
        if not worker:
            worker = AbellaWorker(self.view, response_view)
            workers[worker_key] = worker
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
            self.view.run_command("abella", {"response_view": response_view.id()})
        else:
            self.view.run_command("abella_kill")
            self.view.run_command("abella")

class AbellaKillCommand(sublime_plugin.TextCommand):
    def run(self, edit, close_response=True):
        worker_key = self.view.id()
        worker = workers.get(worker_key, None)
        if worker:
            worker._do_stop()
            worker.join(1)
            if not worker.is_alive():
                del workers[worker_key]
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
            sublime.active_window().show_quick_panel(self.list_items, self._on_select)

    def _on_select(self, idx):
        if idx > -1:
            self.show_thm(self.list_items[idx])
            # sublime.message_dialog(thm)

    def show_thm(self, thm):
        self.panel = self.view.window().create_output_panel('show_thm')
        self.view.window().run_command('show_panel', { 'panel': 'output.show_thm' })
        self.panel.run_command('abella_show', { 'thm': thm, 'view_id': self.view_id });

