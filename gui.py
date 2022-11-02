import asyncio
import functools
import inspect
import os
from pydoc import doc
import re
import sys
import time
from tkinter import *
import tkinter.constants
import tkinter.scrolledtext
from tkinter import ttk, filedialog
import config as cfg
import json
import webbrowser

from telethon import TelegramClient, events, utils, functions, types, custom

# Some configuration for the app
TITLE = 'Telethon GUI'
SIZE = ((310,32), (1024,768), (480,192))
REPLY = re.compile(r'\.r\s*(\d+)\s*(.+)', re.IGNORECASE)
DELETE = re.compile(r'\.d\s*(\d+)', re.IGNORECASE)
EDIT = re.compile(r'\.s(.+?[^\\])/(.*)', re.IGNORECASE)


def get_env(name, message, cast=str):
    if name in os.environ:
        return os.environ[name]
    while True:
        value = input(message)
        try:
            return cast(value)
        except ValueError as e:
            print(e, file=sys.stderr)
            time.sleep(1)


# Session name, API ID and hash to use; loaded from environmental variables
SESSION = os.path.join(os.path.expanduser('~'), cfg.SESSION) #os.environ.get('TG_SESSION', 'gui')
API_ID = cfg.ID #get_env('TG_API_ID', 'Enter your API ID: ', int)
API_HASH = cfg.HASH #get_env('TG_API_HASH', 'Enter your API hash: ')

def class2dict(instance, built_dict={}):
    if not hasattr(instance, "__dict__"):
        print('nothasattr: ', instance)
        return instance
    new_subdic = vars(instance)
    print('vars(instance): ', new_subdic)
    for key, value in new_subdic.items():
        print(new_subdic.items(), key, value)
        new_subdic[key] = class2dict(value)

    print('return: ', new_subdic)
    return new_subdic


def sanitize_str(string):
    return ''.join(x if ord(x) <= 0xffff else
                   '{{{:x}ū}}'.format(ord(x)) for x in string)


def callback(func):
    """
    This decorator turns `func` into a callback for Tkinter
    to be able to use, even if `func` is an awaitable coroutine.
    """
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        result = func(*args, **kwargs)
        if inspect.iscoroutine(result):
            asyncio.create_task(result)

    return wrapped


def allow_copy(widget):
    """
    This helper makes `widget` readonly but allows copying with ``Ctrl+C``.
    """
    widget.bind('<Control-c>', lambda e: None)
    widget.bind('<Key>', lambda e: "break")


class MinPeer(object):
    def __init__(self, object):
        if type(object) is dict:
            for key, value in object.items():
                setattr(self, key, value)
        else:
            self.id = object.id
            self.eid = object.entity.id
            if object.name == '' and object.entity.deleted:
                self.title = '<Deleted_'+str(self.id)+'>'
            else:
                self.title = object.name
            
            if not object.is_user:
                self.username = '<NotAUser>'
            elif object.entity.deleted:
                self.username = '<Deleted>'
            elif object.entity.username is None:
                self.username = '<Empty>'
            else:
                self.username = object.entity.username

            self.date = int(object.date.timestamp())
            """self.selected = False
            self.hidden = False"""

    def __eq__(self, other):
        return (self.id == other.id and
                self.eid == other.eid and
                self.title == other.title and 
                self.username == other.username and
                self.date == other.date)

    def __str__(self):
        return ('MinPeer(id={0}, eid={1}, title="{2}", username="{3}"'
                ', date={4}').format(str(self.id), str(self.eid), str(self.title), str(self.username),
                                                               str(self.date));""", str(self.selected), str(self.hidden))"""

class Pack(object):
    def __init__(self, name, is_started, peers, **kwargs):
        self.name = name
        self.is_started = is_started
        self.peers = peers if type(peers[0]) is MinPeer else [MinPeer(peer) for peer in peers]
    
    def __str__(self):
        return str(self.__class__.__name__)+'('+str(self.__dict__)+')'
        #return 'Pack(name="{0}", is_started={1}, peers={2}'.format(str(self.name), str(self.is_started), str(self.peers))

    def get_dict(self):
        self_dict = dict(vars(self))
        self_dict['peers'] = [dict(vars(peer)) for peer in self.peers]
        return self_dict

class App(tkinter.Tk):
    """
    Our main GUI application; we subclass `tkinter.Tk`
    so the `self` instance can be the root widget.
    One must be careful when assigning members or
    defining methods since those may interfer with
    the root widget.
    You may prefer to have ``App.root = tkinter.Tk()``
    and create widgets with ``self.root`` as parent.
    """
    def __init__(self, client, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cl = client
        self.me = None

        self.packs = []
        self.settings = {"folder_name": "Arbeitsplatz",
                         "local_or_self": "local",
                         "path": os.path.expanduser('~'), # yeah, this is bs... need registry entry?
                         "read_on_startDuty": False}

        self.peer_list = []
        self.input_peer_list = {x.id : x.input_entity for x in self.peer_list} #TODO: capturing fucntion on update?
        self.minPeers_list = []

        self.peer_listbox_items = []
        self.peer_listbox_selected = []
        self.peer_listbox_var = StringVar(value=[])

        self.pack_listbox_items = []
        self.pack_listbox_var = StringVar(value=[])

        self.state = None
        self.pack_onDuty = None
        self.DutyFolderName = self.settings['folder_name']
        self.DutyFolderFilter = types.DialogFilter(
                                    id=255,
                                    title=self.DutyFolderName,
                                    pinned_peers=[],
                                    include_peers=[],
                                    exclude_peers=[]
                                )
        self.DutyFolder = dict(id=255, filter=self.DutyFolderFilter)
        self.muteSettings = dict(
            mute = types.InputPeerNotifySettings(
                    show_previews=True,
                    mute_until=2**31-1,
                    silent=True),
            unmute = types.InputPeerNotifySettings(
                    show_previews=True,
                    mute_until=0,
                    silent=False)
        )

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        self.title(TITLE)
        self.geometry('%dx%d+%d+%d' % (SIZE[0][0], SIZE[0][1], sw/2-SIZE[0][0]/2, sh/2-SIZE[0][1]/2))

        # Signing in row; the entry supports phone and bot token
        self.sign_in_label = Label(self, text='Loading...')
        self.sign_in_label.grid(row=0, column=0)

        self.sign_in_entry = ttk.Entry(self, justify='center')
        self.sign_in_entry.grid(row=0, column=1, sticky=(N,S,E,W))
        self.sign_in_entry.bind('<Return>', self.sign_in)
        self.columnconfigure(1, weight=1)
        
        self.sign_in_button = ttk.Button(self, text='...', command=self.sign_in)
        self.sign_in_button.grid(row=0, column=2, sticky=(N,S,E,W))
        self.code = None

        # Post-init (async, connect client)
        asyncio.create_task(self.post_init())

    def gui_after_sign_in(self):

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry('%dx%d+%d+%d' % (SIZE[1][0], SIZE[1][1], sw/2-SIZE[1][0]/2, sh/2-SIZE[1][1]/2))

        self.rowconfigure(1, weight=1)
        self.main = LabelFrame(self, text='Сборки')
        self.main.grid(sticky=NSEW, row=1, column=0, columnspan=4)

        self.main.rowconfigure(0, pad=5)
        self.main.addPack_button = ttk.Button(self.main, text='+', width=2, command=self.addPack, state='disabled')
        self.main.addPack_button.grid(row=0,column=0,sticky=(E,W))

        self.main.columnconfigure(1, pad=1, weight=10)
        self.packs_entry_var = StringVar()
        self.packs_entry_var.trace('w', self.validatePackname)
        self.main.packsList_combobox = ttk.Combobox(self.main, textvariable=self.packs_entry_var, state='disabled')
        self.main.packsList_combobox.grid(row=0,column=1,sticky=(E,W), padx=2)
        self.main.packsList_combobox.bind('<<ComboboxSelected>>', self.fillPackPeers)

        self.main.editPack_button = ttk.Button(self.main, text='Ред.', command=self.editPack, state='disabled')
        self.main.editPack_button.grid(row=0,column=2,sticky=W, padx=5)

        self.main.columnconfigure(3, weight=10)
        self.searchPeers_entry_var = StringVar()
        self.searchPeers_entry_var.trace('w', self.searchPeers)
        self.main.searchPeers_entry = ttk.Entry(self.main, textvariable=self.searchPeers_entry_var, state='disabled')
        self.main.searchPeers_entry.grid(row=0,column=3,sticky=(E,W))

        self.main.refreshPeers_button = ttk.Button(self.main, text='⭮', width=2, command=self.fillPeers, state='disabled')
        self.main.refreshPeers_button.grid(row=0,column=4)

        #self.main.rowconfigure(1, weight=1)
        self.main.packPeers_listbox = Listbox(self.main,
                                            activestyle='dotbox',
                                            selectmode='multiple',
                                            listvariable=self.pack_listbox_var,
                                            exportselection=False,
                                            state='disabled')
        self.main.packPeers_listbox.grid(row=1,column=0,rowspan=4,columnspan=2, sticky=NSEW)
        self.main.packPeers_listbox.bind('<<ListboxSelect>>', self.pack_listbox_handler)
        self.main.left_scrollbar = ttk.Scrollbar(self.main.packPeers_listbox, orient='vertical')
        self.main.left_scrollbar.pack(side='right', fill='y')
        self.main.packPeers_listbox.config(yscrollcommand=self.main.left_scrollbar.set)
        self.main.left_scrollbar.config(command=self.main.packPeers_listbox.yview)

        self.main.delPack_button = ttk.Button(self.main, text='Удалить\nсборку', command=self.delPack, state='disabled')
        self.main.delPack_button.grid(row=1,column=2,sticky=(N, W, E), padx=5)

        self.main.cancelEdit_button = ttk.Button(self.main, text='Отмена', command=self.cancelEdit, state='disabled')
        self.main.cancelEdit_button.grid(row=2,column=2,sticky=(N, W, E), padx=5)
        
        self.main.rowconfigure(3, weight=1)
        self.main.addPeers_button = ttk.Button(self.main, text='<- Добавить', command=self.addPeers, state='disabled')
        self.main.addPeers_button.grid(row=3,column=2,sticky=(S, W, E), padx=5)
        
        self.main.peers_listbox = Listbox(self.main,
                                        activestyle='dotbox',
                                        selectmode='multiple',
                                        listvariable=self.peer_listbox_var,
                                        exportselection=False)
        self.main.peers_listbox.grid(row=1,column=3,rowspan=4,columnspan=2, sticky=NSEW)
        self.main.peers_listbox.bind('<<ListboxSelect>>', self.peers_listbox_handler)
        self.main.right_scrollbar = ttk.Scrollbar(self.main.peers_listbox, orient='vertical')
        self.main.right_scrollbar.pack(side='right', fill='y')
        self.main.peers_listbox.config(yscrollcommand=self.main.right_scrollbar.set)
        self.main.right_scrollbar.config(command=self.main.peers_listbox.yview)

        self.main.rowconfigure(4, weight=1)
        self.main.delPeers_button = ttk.Button(self.main, text='Убрать ->', command=self.delPeers, state='disabled')
        self.main.delPeers_button.grid(row=4,column=2,sticky=(N, W, E), padx=5)

        self.main.main_button = ttk.Button(self.main, text='Нужно выбрать сборку, милорд...', command=self.mainButton_handler, state='disabled')
        self.main.main_button.grid(row=5,column=0,columnspan=2, sticky=(E,W), pady=5)

        self.main.peerCount_label = ttk.Label(self.main, justify='right')
        self.main.peerCount_label.grid(row=5, column=4, sticky=(N, E))


        self.progressbar = ttk.Progressbar(self, mode='indeterminate')
        self.progressbar.grid(row=2, column=0, columnspan=2, sticky=(N, E, S, W))

        self.settings_button = ttk.Button(self, text='Настройки', command=self.callSettings)
        self.settings_button.grid(row=2, column=2)

        return

    def callSettings(self):

        self.wm_attributes('-disabled', True)
        xp = self.winfo_rootx()
        yp = self.winfo_rooty()
        x = self.winfo_width()
        y = self.winfo_height()
        geom = "%dx%d+%d+%d" % (SIZE[2][0], SIZE[2][1], x/2-SIZE[2][0]/2+xp, y/2-SIZE[2][1]/2+yp)
        print(x, y, geom)

        self.s_modal = Toplevel(self)
        self.s_modal.title(TITLE)
        self.s_modal.geometry(geom)
        self.s_modal.transient(self)
        self.s_modal.protocol("WM_DELETE_WINDOW", self.s_modal_onclose)

        self.s_modal.where_label = ttk.Label(self.s_modal, justify='right', text='Хранить настройки:\n(пока только локально) ')
        self.s_modal.where_label.grid(row=0, column=0, rowspan=2, ipadx=5, sticky=(E))
        self.s_modal.path_label = ttk.Label(self.s_modal, justify='right', text='Путь к файлу настроек:\n(пока только домашняя папка) ') #text mutable
        self.s_modal.path_label.grid(row=2, column=0, ipadx=5, sticky=(E))
        self.s_modal.fName_label = ttk.Label(self.s_modal, justify='right', text='Название рабочей папки:')
        self.s_modal.fName_label.grid(row=3, column=0, ipadx=5, sticky=(E))
        self.s_modal.read_label = ttk.Label(self.s_modal, justify='right', text='Всё прочитать при старте сборки:')
        self.s_modal.read_label.grid(row=4, column=0, ipadx=5, sticky=(E))


        self.s_modal.columnconfigure(0, pad=10)
        self.s_modal.columnconfigure(1, weight=1, pad=10)
        self.s_modal.columnconfigure(2, pad=10)
        self.s_modal.local_or_self = StringVar()
        self.s_modal.where1_radio = ttk.Radiobutton(self.s_modal, text='Локально', variable=self.s_modal.local_or_self, value='local')
        self.s_modal.where1_radio.grid(row=0, column=1, sticky=(W))
        self.s_modal.where2_radio = ttk.Radiobutton(self.s_modal, text='Чат "Избранное"', variable=self.s_modal.local_or_self, value='self', state='disabled') # TODO: find a way for link to favs
        self.s_modal.where2_radio.grid(row=1, column=1, sticky=(W))
        self.s_modal.local_or_self.set(self.settings['local_or_self'])

        self.s_modal.where_button = ttk.Button(self.s_modal, text='Открыть', command=self.openSavingPlace)
        self.s_modal.where_button.grid(row=0, column=2, rowspan=2, sticky=(E,W))
        self.s_modal.path_var = StringVar()
        self.s_modal.path_entry = ttk.Entry(self.s_modal, text=self.s_modal.path_var, exportselection=0, state='readonly')
        self.s_modal.path_entry.grid(row=2, column=1, ipady=1, sticky=(E, W))
        self.s_modal.path_var.set(self.settings['path'])
        self.s_modal.path_button = ttk.Button(self.s_modal, text='Выбрать', command=self.selectSavingFolder, state='disabled') #TODO: find a way for keep path
        self.s_modal.path_button.grid(row=2, column=2, sticky=(E, W))
        self.s_modal.fName_var = StringVar()
        self.s_modal.fName_entry = ttk.Entry(self.s_modal, textvariable=self.s_modal.fName_var)
        self.s_modal.fName_entry.grid(row=3, column=1, columnspan=2, ipady=1, sticky=(E, W))
        self.s_modal.fName_var.set(self.settings['folder_name'])
        self.s_modal.read_var = BooleanVar()
        self.s_modal.read_checkbutton = ttk.Checkbutton(self.s_modal, variable=self.s_modal.read_var, onvalue=True, offvalue=False)
        self.s_modal.read_checkbutton.grid(row=4, column=1, sticky=(W))
        self.s_modal.read_var.set(self.settings['read_on_startDuty'])

        self.s_modal.save_button = ttk.Button(self.s_modal, text='Применить', command=self.saveSettings)
        self.s_modal.save_button.grid(row=5, column=0, columnspan=3, sticky=(S, N))

        self.s_modal.rowconfigure(2, pad=10)
        self.s_modal.rowconfigure(3, pad=10)
        self.s_modal.rowconfigure(4, pad=10)
        self.s_modal.rowconfigure(5, pad=10)

        print(self.settings)

    def openSavingPlace(self):
        if self.s_modal.local_or_self.get() == 'local':
            systems = {
                'nt': os.startfile,
                'posix': lambda foldername: os.system('xdg-open "%s"' % foldername),
                'os2': lambda foldername: os.system('open "%s"' % foldername)
                }

            systems.get(os.name, os.startfile)(self.s_modal.path_var.get())
        elif self.s_modal.local_or_self.get() == 'self':
            webbrowser.open_new_tab(self.s_modal.path_var.get())
        return

    def selectSavingFolder(self):
        askdir = filedialog.askdirectory(initialdir=self.s_modal.path_var.get(), parent=self.s_modal)
        if askdir:
            self.s_modal.path_var.set(askdir)
        return

    def saveSettings(self):
        self.settings['folder_name'] = self.s_modal.fName_var.get()
        self.settings['read_on_startDuty'] = self.s_modal.read_var.get()
        self.writeSettings()
        self.s_modal_onclose()
        self.readPacks(self.main)
        return

    def s_modal_onclose(self):
        self.wm_attributes('-disabled', False)
        self.s_modal.destroy()
        self.deiconify()

    async def post_init(self):
        """
        Completes the initialization of our application.
        Since `__init__` cannot be `async` we use this.
        """
        if await self.cl.is_user_authorized():
            await self.set_signed_in(await self.cl.get_me())
        else:
            # User is not logged in, configure the button to ask them to login
            self.sign_in_button['text'] = 'Sign in'
            self.sign_in_label['text'] = 'Sign in (phone/token):'

    # noinspection PyUnusedLocal
    @callback
    async def sign_in(self, event=None):
        """
        Note the `event` argument. This is required since this callback
        may be called from a ``widget.bind`` (such as ``'<Return>'``),
        which sends information about the event we don't care about.
        This callback logs out if authorized, signs in if a code was
        sent or a bot token is input, or sends the code otherwise.
        """
        # if button was waiting to log out
        self.sign_in_label.config(text='Working...')
        #self.sign_in_entry.state(['disabled'])
        if await self.cl.is_user_authorized():
            await self.cl.log_out()
            self.destroy()
            return

        # if button was waiting to sign in, number or token was passed
        value = self.sign_in_entry.get().strip()
        if self.code:
           await self.set_signed_in(await self.cl.sign_in(code=value))
        elif ':' in value:
           await self.set_signed_in(await self.cl.sign_in(bot_token=value))
        else:
            self.code = await self.cl.send_code_request(value)
            self.sign_in_label.config(text='Code:')
            self.sign_in_entry.delete(0, 'end')
            #self.sign_in_entry.state(['!disabled', '!readonly'])
            self.sign_in_entry.focus()
            return

### Сразу после авторизации
    async def set_signed_in(self, me):
        """
        Configures the application as "signed in" (displays user's
        name and disables the entry to input phone/bot token/code).
        """
        self.me = me
        self.sign_in_label['text'] = 'Signed in'
        #self.sign_in_entry.state(['!readonly'])
        self.sign_in_entry.delete(0, 'end')
        self.sign_in_entry.insert(0, utils.get_display_name(me))
        self.sign_in_entry.state(['disabled'])
        self.sign_in_button.config(text='Log out')

        self.gui_after_sign_in()

        self.progressbar.step(25.0)
        self.progressbar.start(5)
        async for Dialog in self.cl.iter_dialogs():
            if (hasattr(Dialog.entity, 'migrated_to')
            and Dialog.entity.migrated_to is not None): continue

            self.peer_list.append(Dialog)
            self.minPeers_list.append(MinPeer(Dialog))
            self.input_peer_list[Dialog.id] = Dialog.input_entity

        #self.cl.add_event_handler(self.updates_handler)

        self.readPacks(self.main)


    
    def peers_listbox_handler(self, event=None):
        current = self.main.peers_listbox.curselection()

        for i, item in enumerate(self.peer_listbox_items):
            if (i in current) and (item not in self.peer_listbox_selected):
                self.peer_listbox_selected.append(item)
            elif (i not in current) and (item in self.peer_listbox_selected):
                self.peer_listbox_selected.remove(item)

        if len(self.peer_listbox_selected) == 0:
            self.main.addPeers_button.state(['disabled'])
        else:
            self.main.addPeers_button.state(['!disabled'])

    def pack_listbox_handler(self, event=None):
        if self.state in ('addPack', 'editPack', 'editDuty'):
            if len(self.main.packPeers_listbox.curselection()) == 0:
                self.main.delPeers_button.state(['disabled'])
            else:
                self.main.delPeers_button.state(['!disabled'])

    def validatePackname(self, *args):
        if self.state == 'normal': return

        typed = self.packs_entry_var.get().casefold()
        packs_name = list(map(lambda x: x.casefold() ,self.main.packsList_combobox['values']))
        if typed in packs_name:
            self.main.editPack_button.config(state='disabled')
        else:
            self.main.editPack_button.config(state='enabled')
    
    def readPacks(self, gui):
        self.state = 'normal'

        try:
            with open(os.path.join(self.settings['path'], 'tgpacks_config.json'), 'r', encoding='utf-8') as file:
                content = json.load(file)
        except FileNotFoundError:
            print('FileNotFoundError')
            self.callSettings()
            return
        
        self.progressbar.stop()
        self.progressbar.config(mode='determinate')
        self.progressbar['value'] = 100

        if content == None: return
        self.settings = content['settings']
        self.packs = [Pack(**pack) for pack in content['packs']]
        if len(self.packs) == 0:
            gui.main_button['text'] = 'Амбар пуст, милорд...'
            gui.addPack_button.state(['!disabled'])
            return

        gui.packsList_combobox.set('Выбери сборку:')
        gui.packsList_combobox.state(['!disabled', 'readonly'])
        gui.addPack_button.state(['!disabled'])
        
        gui.packsList_combobox['values'] = [pack.name for pack in self.packs]

    def pre_write(self):

        if self.state == 'addPack':
            obj={"name":self.packs_entry_var.get(),
                "is_started":False,
                "peers":self.pack_listbox_items}#,
                #"pinned_peers": [peer for peer in self.pack_listbox_items if peer.pinned]}
            self.packs.append(Pack(**obj))
            self.main.packsList_combobox['values'] = [pack.name for pack in self.packs]
            self.main.packsList_combobox.current('end')
        elif self.state == 'editPack':
            self.packs[self.main.packsList_combobox.current()].name = self.packs_entry_var.get()
            self.packs[self.main.packsList_combobox.current()].peers = self.pack_listbox_items
            self.main.packsList_combobox['values'] = [pack.name for pack in self.packs]
        elif self.state == 'editDuty':
            2
            #self.pack_onDuty['onduty_include']
            #self.pack_onDuty['onduty_exclude']
            #self.pack_onDuty['onduty_pinned']


        if self.state in ('addPack', 'editPack'):
            self.state = 'normal'
            self.fillPackPeers()
            self.main.packsList_combobox.state(['!disabled','readonly'])
            self.main.addPack_button.state(['!disabled'])
            #self.main.packPeers_listbox['state'] = 'disabled'
            self.main.editPack_button.state(['!disabled'])
            self.main.editPack_button.config(text='Ред.', command=self.editPack)
            self.main.addPeers_button.state(['disabled'])
            self.main.delPeers_button.state(['disabled'])
            self.main.cancelEdit_button.state(['disabled'])
            self.main.searchPeers_entry.state(['disabled'])
            self.main.refreshPeers_button.state(['disabled'])
            self.main.peers_listbox['state'] = 'disabled'
            self.peer_listbox_var.set([])
            self.main.peerCount_label['text'] = ''

        self.writeSettings()
        return
    
    def writeSettings(self):
        packs = {"packs": [pack.get_dict() for pack in self.packs]}
        settings = {"settings": self.settings}

        content = dict(**settings, **packs)

        with open(os.path.join(self.settings['path'], 'tgpacks_config.json'), 'w', encoding='utf-8') as file:
            json.dump(content, file, ensure_ascii=False, indent=4)
        return

    @callback
    async def fillPeers(self):
        self.main.editPack_button.state(['disabled'])
        self.main.cancelEdit_button.state(['disabled'])
        self.main.searchPeers_entry.delete(0, 'end')
        self.main.searchPeers_entry.state(['disabled'])
        self.main.refreshPeers_button.state(['disabled'])
        self.main.peers_listbox.selection_clear(0, 'end')
        self.main.peers_listbox['state'] = 'disabled'
        self.main.delPeers_button.state(['disabled'])

        #TODO: stop updating peers_list
        self.peer_listbox_items.clear()
        self.peer_listbox_var.set([])

        pack_ids = [item.id for item in self.pack_listbox_items]
        self.peer_listbox_items = [peer for peer in self.minPeers_list if peer.id not in pack_ids]
        self.peer_listbox_var.set([item.title for item in self.peer_listbox_items])

        self.main.peerCount_label['text'] = self.main.peers_listbox.size()
        self.main.peers_listbox['state'] = 'normal'
        self.main.searchPeers_entry.state(['!disabled'])
        self.main.refreshPeers_button.state(['!disabled'])
        self.main.editPack_button.state(['!disabled'])
        self.main.cancelEdit_button.state(['!disabled'])
        
    def fillPackPeers(self, event=None):
        pack_id = self.main.packsList_combobox.current()

        if self.state == 'normal':
            self.main.editPack_button.state(['!disabled'])
            self.main.main_button.config(text='Запустить сборку '+str(self.packs[pack_id].name))
            self.main.main_button.state(['!disabled'])
        elif (self.state in ('onDuty', 'expectChange')) and (str(self.pack_onDuty.name) != str(self.packs[pack_id].name)):
            self.state = 'expectChange'
            self.main.main_button.config(text='Поменять сборку:\n'+str(self.pack_onDuty.name)+' ---> '+str(self.packs[pack_id].name))
            self.main.main_button.state(['!disabled'])
            self.main.editPack_button.state(['disabled'])
        elif self.state == 'onDuty' and (str(self.pack_onDuty.name) == str(self.packs[pack_id].name)):
            return
        elif self.state == 'expectChange' and (str(self.pack_onDuty.name) == str(self.packs[pack_id].name)):
            self.state = 'onDuty'
            self.pack_listbox_items = self.pack_onDuty.peers.copy()
            self.pack_listbox_var.set([item.title for item in self.pack_listbox_items])
            self.main.main_button.config(text='Завершить сборку '+str(self.pack_onDuty.name))
            self.main.main_button.state(['!disabled'])
            self.main.editPack_button.state(['!disabled'])
            self.main.packPeers_listbox['state'] = 'normal'
            #TODO: support added/deleted peers marks
            return

        self.pack_listbox_items = self.packs[pack_id].peers.copy()
        self.pack_listbox_var.set([item.title for item in self.pack_listbox_items])
        self.main.packPeers_listbox['state'] = 'normal'

    @callback
    async def addPack(self):
        self.state = 'addPack'
        self.fillPeers()

        self.pack_listbox_items.clear()
        self.pack_listbox_var.set([])
        self.main.main_button.state(['disabled'])
        self.main.packsList_combobox.state(['!disabled', '!readonly'])
        self.main.packsList_combobox.set('<Придумай имя сборки>')
        self.main.packsList_combobox.select_range(0, 'end')
        self.main.packsList_combobox.focus()
        self.main.packPeers_listbox['state'] = 'normal'
        self.main.addPack_button.state(['disabled'])
        self.main.editPack_button.config(text='✔', command=self.pre_write)

    def editPack(self):
        if self.state == 'normal':
            self.state = 'editPack'
            self.main.packsList_combobox.state(['!readonly'])
            self.main.editPack_button.config(text='✔', command=self.pre_write)
            self.main.delPack_button.state(['!disabled'])
        elif self.state == 'onDuty':
            self.state = 'editDuty'
            self.main.packsList_combobox.state(['disabled'])
            self.main.editPack_button.state(['disabled'])

        self.fillPeers()

        
        self.main.main_button.config(state='disabled')
        self.main.addPack_button.config(state='disabled')
        return

    def cancelEdit(self):
        if self.state == 'addPack':
            self.state = 'normal'
            self.main.packsList_combobox.set('Выбери сборку:')
            self.main.packPeers_listbox['state'] = 'disabled'
            self.main.editPack_button.state(['disabled'])
            self.main.delPack_button.state(['disabled'])
            self.main.main_button.config(text='Нужно выбрать сборку, милорд...')
            self.pack_listbox_items.clear()
            self.pack_listbox_var.set([])
        elif self.state == 'editPack':
            self.state = 'normal'
            self.main.editPack_button.state(['!disabled'])
            self.fillPackPeers()
        elif self.state == 'editDuty':
            self.state = 'onDuty'
            self.main.editPack_button.state(['!disabled'])
            self.main.main_button.state(['!disabled'])

        if self.state == 'normal':
            self.main.addPack_button.state(['!disabled'])

        self.main.packsList_combobox.state(['!disabled', 'readonly'])

        self.main.editPack_button.config(text='Ред.', command=self.editPack)
        
        self.main.addPeers_button.state(['disabled'])
        self.main.delPeers_button.state(['disabled'])
        self.main.searchPeers_entry.delete(0, 'end')
        self.main.searchPeers_entry.state(['disabled'])
        self.main.refreshPeers_button.state(['disabled'])
        self.peer_listbox_selected = []
        self.peer_listbox_var.set([])
        self.main.peers_listbox['state'] = 'disabled'
        self.main.cancelEdit_button.state(['disabled'])
        self.main.peerCount_label['text'] = ''
        return
    
    def delPack(self):
        selected_pack = self.main.packsList_combobox.current()
        del self.packs[selected_pack]
        self.main.packsList_combobox['values'] = [pack.name for pack in self.packs]
        self.state = 'addPack'
        self.cancelEdit()
        self.writeSettings()
        return

    @callback
    async def addPeers(self):
        selected = self.main.peers_listbox.curselection()
        forDuty = self.peer_listbox_selected.copy() #TODO: need better algorithm

        for i in selected:
            self.main.peers_listbox.selection_clear(i)

        for peer in self.peer_listbox_selected.copy():
            self.peer_listbox_selected.remove(peer)
            self.pack_listbox_items.append(peer)
            if peer in self.peer_listbox_items:
                self.peer_listbox_items.remove(peer)

        self.peer_listbox_var.set([item.title for item in self.peer_listbox_items])
        self.pack_listbox_var.set([item.title for item in self.pack_listbox_items])

        self.main.addPeers_button.state(['disabled'])

        if self.state == 'editDuty':
            #TODO: mark in listbox added while onDuty
            self.pack_onDuty.peers.extend(forDuty)
            await self.duty_Update([self.input_peer_list[sel.id] for sel in forDuty], True)
            return

        self.main.editPack_button.state(['!disabled'])

        return

    @callback
    async def delPeers(self):
        selected = self.main.packPeers_listbox.curselection()

        pack_listbox_selected = []
        for i, item in enumerate(self.pack_listbox_items):
            if i in selected: pack_listbox_selected.append(item)

        for peer in pack_listbox_selected:
            self.pack_listbox_items.remove(peer)
            peer.date = [x.date for x in self.minPeers_list if x.id == peer.id][0]
            self.peer_listbox_items.append(peer)
        
        self.main.packPeers_listbox.selection_clear(0, 'end')

        self.peer_listbox_items.sort(key=lambda y: y.date, reverse=True)
        self.peer_listbox_var.set([item.title for item in self.peer_listbox_items])
        self.pack_listbox_var.set([item.title for item in self.pack_listbox_items])
        
        self.main.delPeers_button.state(['disabled'])
        if not len(self.pack_listbox_items):
            self.main.editPack_button.state(['disabled'])
        
        if self.state == 'editDuty':
            #TODO: mark deleted
            for peer in pack_listbox_selected:
                print(peer)
                self.pack_onDuty.peers.remove(peer)
            await self.duty_Update([self.input_peer_list[sel.id] for sel in pack_listbox_selected], False)
        return
    
    def refreshPeers():
        return

    def searchPeers(self, *args):
        substr = self.searchPeers_entry_var.get().casefold()
        pack_ids = [item.id for item in self.pack_listbox_items]
        self.peer_listbox_items = list(
                                    filter(lambda peer: (peer.id not in pack_ids) and (str(peer.id).casefold().find(substr) !=-1
                                                                                    or peer.username.casefold().find(substr) !=-1
                                                                                    or peer.title.casefold().find(substr) !=-1),
                                            self.minPeers_list)
                                        )

        self.main.peers_listbox.selection_clear(0, 'end')
        self.peer_listbox_var.set([item.title for item in self.peer_listbox_items])

        for i, peer in enumerate(self.peer_listbox_items):
            if peer in self.peer_listbox_selected:
                self.main.peers_listbox.selection_set(i)

        self.main.peerCount_label['text'] = self.main.peers_listbox.size()


    async def duty_Update(self, peers, is_adding):
        for peer in peers:
            if is_adding:
                self.DutyFolderFilter.include_peers.append(peer)
                await self.cl(functions.account.UpdateNotifySettingsRequest(
                    peer = peer,
                    settings=self.muteSettings['unmute']))
            else:
                self.DutyFolderFilter.include_peers.remove(peer)
                await self.cl(functions.account.UpdateNotifySettingsRequest(
                    peer = peer,
                    settings=self.muteSettings['mute']))
        
        await self.cl(functions.messages.UpdateDialogFilterRequest(**self.DutyFolder))
        return
       
    async def duty_Start(self, pack):
        input_peers = [self.input_peer_list[x.id] for x in pack.peers]

        self.DutyFolderFilter.include_peers = input_peers

        await self.cl(functions.messages.UpdateDialogFilterRequest(**self.DutyFolder))

        for peer in input_peers:
            await self.cl(functions.account.UpdateNotifySettingsRequest(
                peer = peer,
                settings=self.muteSettings['unmute']))
            if self.settings['read_on_startDuty']:
                await self.cl.send_read_acknowledge(peer)

        pack.is_started = True
        return
    
    async def duty_Change(self, old_pack, new_pack):
        await self.duty_Finish(old_pack)
        await self.duty_Start(new_pack)
        return

    async def duty_Finish(self, cur_pack):
        #TODO: return pack peers in listbox to initial state
        input_peers = [self.input_peer_list[x.id] for x in cur_pack.peers]

        for peer in input_peers:
            await self.cl(functions.account.UpdateNotifySettingsRequest(
                peer = peer,
                settings=self.muteSettings['mute']))

        await self.cl(functions.messages.UpdateDialogFilterRequest(id=255))
        cur_pack.is_started = False
        return    
    
    @callback
    async def mainButton_handler(self):
        selected_pack_index = self.main.packsList_combobox.current()
        #selected_pack = self.packs[selected_pack_index]
        #selected_pack.peers = self.packs[selected_pack_index].peers.copy()
        selected_pack = Pack(**self.packs[selected_pack_index].get_dict())

        self.main.main_button.state(['disabled'])

        if self.state in ('normal', 'expectChange'):
            #self.packs[selected_pack].is_started = True

            if self.state == 'normal':
                self.pack_onDuty = selected_pack
                await self.duty_Start(self.pack_onDuty)
                self.main.addPack_button.state(['disabled'])
            else:
                await self.duty_Change(self.pack_onDuty, selected_pack)

                self.pack_onDuty = selected_pack
            self.main.main_button.config(text='Завершить сборку '+str(self.pack_onDuty.name))
            self.state = 'onDuty'
        elif self.state == 'onDuty':
            await self.duty_Finish(self.pack_onDuty)
            self.main.addPack_button.state(['!disabled'])
            self.pack_onDuty = []
            self.main.main_button.config(text='Запустить сборку '+str(selected_pack.name))
            self.state = 'normal'

        self.main.main_button.state(['!disabled'])

        return
    
    async def updates_handler(self, update):
        print('NEW: ', update)



async def main(interval=0.05):
    client = TelegramClient(SESSION, API_ID, API_HASH)
    try:
        await client.connect()
    except Exception as e:
        print('Failed to connect', e, file=sys.stderr)
        return

    app = App(client)
    try:
        while True:
            # We want to update the application but get back
            # to asyncio's event loop. For this we sleep a
            # short time so the event loop can run.
            #
            # https://www.reddit.com/r/Python/comments/33ecpl
            app.update()
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        pass
    except tkinter.TclError as e:
        if 'application has been destroyed' not in e.args[0]:
            raise
    finally:
        await app.cl.disconnect()


if __name__ == "__main__":
    asyncio.run(main())