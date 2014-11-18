#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  metalink.py
#
#  Code from pm2ml Copyright (C) 2012-2013 Xyne
#  Copyright © 2013,2014 Antergos
#
#  This file is part of Cnchi.
#
#  Cnchi is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  Cnchi is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Cnchi; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

""" Operations with metalinks """

import logging
import tempfile
import os
import xml
import hashlib
import re

from collections import deque

try:
    import pyalpm
except ImportError:
    pass

'''
from pycman import config

import errno
import sys
'''

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

# Make reflector support optional.
try:
  from Reflector import MirrorStatus as MS
  REPOSITORIES = MS.REPOSITORIES
  MIRROR_URL_FORMAT = MS.MIRROR_URL_FORMAT
except ImportError:
  REPOSITORIES = set()
  MIRROR_URL_FORMAT = None

def get_info(metalink):
    """ Reads metalink xml and stores it in a dict """

    metalink_info = {}

    tag = "{urn:ietf:params:xml:ns:metalink}"

    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.write(str(metalink).encode('UTF-8'))
    temp_file.close()
    
    element = {}

    for event, elem in ET.iterparse(temp_file.name, events=('start', 'end')):
        if event == "start":
            if elem.tag.endswith("file"):
                element['filename'] = elem.attrib['name']
            elif elem.tag.endswith("identity"):
                element['identity'] = elem.text
            elif elem.tag.endswith("size"):
                element['size'] = elem.text
            elif elem.tag.endswith("version"):
                element['version'] = elem.text
            elif elem.tag.endswith("description"):
                element['description'] = elem.text
            elif elem.tag.endswith("hash"):
                element['hash'] = elem.text
            elif elem.tag.endswith("url"):
                try:
                    element['urls'].append(elem.text)
                except KeyError as err:
                    element['urls'] = [elem.text]
        if event == "end":
            if elem.tag.endswith("file"):
                # Crop to 5 urls max for file
                element['urls'] = element['urls'][:5]
                metalink_info[element['identity']] = element.copy()
                element.clear()
                elem.clear()

    if os.path.exists(temp_file.name):
        os.remove(temp_file.name)

    return metalink_info

def create(package_name, pacman_conf_file):
    """ Creates a metalink to download package_name and its dependencies """

    args = ["-c", pacman_conf_file, "--noconfirm", "--all-deps"]

    if package_name is "databases":
        args += ["--refresh"]

    if package_name is not "databases":
        args += [package_name]

    try:
        download_queue, not_found, missing_deps = build_download_queue(args=options)
    except Exception as err:
        msg = _("Unable to create download queue for package %s") % package_name
        logging.error(msg)
        logging.exception(err)
        return None

    if not_found:
        msg = _("Can't find these packages: ")
        for not_found in sorted(not_found):
            msg = msg + not_found + " "
        logging.warning(msg)

    if missing_deps:
        msg = _("Warning! Can't resolve these dependencies: ")
        for missing in sorted(missing_deps):
            msg = msg + missing + " "
        logging.warning(msg)

    metalink = download_queue_to_metalink(download_queue)
    
    return metalink

# From here comes modified code from pm2ml
# pm2ml is Copyright (C) 2012-2013 Xyne
# http://xyne.archlinux.ca/projects/pm2ml

def download_queue_to_metalink(download_queue):
    impl = xml.dom.minidom.getDOMImplementation()
    metalink = Metalink(impl)

    for db, sigs in download_queue.dbs:
        metalink.add_db(db, sigs)

    for pkg, urls, sigs in download_queue.sync_pkgs:
        metalink.add_sync_pkg(pkg, urls, sigs)

    for pkg in download_queue.aur_pkgs:
        metalink.append_aur_pkg(pkg)

    return metalink

class Metalink():
    def __init__(self, impl):
        self.doc = impl.createDocument(None, "metalink", None)
        self.doc.documentElement.setAttribute('xmlns', "urn:ietf:params:xml:ns:metalink")
        self.files = self.doc.documentElement

    def __str__(self):
        return re.sub(
          r'(?<=>)\n\s*([^\s<].*?)\s*\n\s*',
          r'\1',
          self.doc.toprettyxml(indent=' ')
        )

    def add_urls(self, element, urls):
        """Add URL elements to the given element."""
        for url in urls:
          url_tag = self.doc.createElement('url')
          element.appendChild(url_tag)
          url_val = self.doc.createTextNode(url)
          url_tag.appendChild(url_val)

    def add_sync_pkg(self, pkg, urls, sigs=False):
        """Add a sync db package."""
        file_ = self.doc.createElement("file")
        file_.setAttribute("name", pkg.filename)
        self.files.appendChild(file_)
        for tag, db_attr, attrs in (
            ('identity', 'name', ()),
            ('size', 'size', ()),
            ('version', 'version', ()),
            ('description', 'desc', ()),
            ('hash', 'sha256sum', (('type','sha256'),)),
            ('hash', 'md5sum', (('type','md5'),))):
            tag = self.doc.createElement(tag)
            file_.appendChild(tag)
            val = self.doc.createTextNode(str(getattr(pkg, db_attr)))
            tag.appendChild(val)
            for key, val in attrs:
                tag.setAttribute(key, val)
        urls = list(urls)
        self.add_urls(file_, urls)
        if sigs:
            self.add_file(pkg.filename + '.sig', (u + '.sig' for u in urls))

    def add_file(self, name, urls):
        """Add a signature file."""
        file_ = self.doc.createElement("file")
        file_.setAttribute("name", name)
        self.files.appendChild(file_)
        self.add_urls(file_, urls)

    def add_db(self, db, sigs=False):
        """Add a sync db."""
        file_ = self.doc.createElement("file")
        name = db.name + '.db'
        file_.setAttribute("name", name)
        self.files.appendChild(file_)
        urls = list(os.path.join(url, db.name + '.db') for url in db.servers)
        self.add_urls(file_, urls)
        if sigs:
            self.add_file(name + '.sig', (u + '.sig' for u in urls))

class PkgSet(object):
    """ Represents a set of packages """
    def __init__(self, pkgs=[]):
        """ Init our internal self.pkgs dict with all given packages in pkgs """
        self.pkgs = dict()
        for pkg in pkgs:
            self.pkgs[pkg.name] = pkg

    def __repr__(self):
        return 'PkgSet(%s)' % repr(self.pkgs)

    def add(self, pkg):
        self.pkgs[pkg.name] = pkg

    def ignore(self, pkgs, groups):
        for name, pkg in tuple(self.pkgs.items()):
            if name in pkgs:
                del self.pkgs[name]
            else:
                for grp in pkg.groups:
                    if grp in groups:
                        del self.pkgs[name]
                        break

    def __and__(self, other):
        new = PkgSet(set(self.pkgs.values()) & set(other.pkgs.values()))
        return new

    def __iand__(self, other):
        self.pkgs = self.__and__(other).pkgs
        return self

    def __or__(self, other):
        copy = PkgSet(self.pkgs.values())
        return copy.__ior__(other)

    def __ior__(self, other):
        self.pkgs.update(other.pkgs)
        return self

    def __contains__(self, pkg):
        return pkg.name in self.pkgs

    def __iter__(self):
        for v in self.pkgs.values():
            yield v

    def __len__(self):
        return len(self.pkgs)

class DownloadQueue():
    """ Represents a download queue """
    def __init__(self):
        self.dbs = list()
        self.sync_pkgs = list()

    def __bool__(self):
        return bool(self.dbs or self.sync_pkgs)

    def __nonzero__(self):
        return self.dbs or self.sync_pkgs

    def add_db(self, db, sigs=False):
        self.dbs.append((db, sigs))

    def add_sync_pkg(self, pkg, urls, sigs=False):
        self.sync_pkgs.append((pkg, urls, sigs))

def build_download_queue(args=None):
    """ Function to build a download queue.
        Needs a pkgname in args """

    pargs = parse_args(args)
    
    conf_file = pargs.conf
    
    try:
        pacman = Pac(conf_file)
    except Exception as err:
        logging.error(err)
        logging.error("Can't initialize pyalpm: %s" % err)
        return None, None, None
    
    handle = pacman.get_handle()
    conf = pacman.get_config()

    requested = set(pargs.pkgs)
    ignored = set(pargs.ignore)
    ignoredgroups = set(pargs.ignoregroup)
    official = PkgSet()
    other = PkgSet()
    foreign_names = set()
    missing_deps = list()
    found = set()
    not_found = set()

    global REPOSITORIES

    for pkg in requested:
        official_grp = PkgSet()
        other_grp = PkgSet()
        for db in handle.get_syncdbs():
            syncpkg = db.get_pkg(pkg)
            if syncpkg:
                if db.name in REPOSITORIES:
                    official.add(syncpkg)
                else:
                    other.add(syncpkg)
                break
            else:
                syncgrp = db.read_grp(pkg)
                if syncgrp:
                    found.add(pkg)
                    if db.name in REPOSITORIES:
                        official_grp |= PkgSet(syncgrp[1])
                    else:
                        other_grp |= PkgSet(syncgrp[1])
        else:
            if pargs.ask and not pargs.noconfirm:
                selected = select_grp(pkg, official_grp | other_grp, ignored)
                official |= (official_grp & selected)
                other |= (other_grp & selected)
            else:
                official |= official_grp
                other |= other_grp

    foreign_names = requested - set(x.name for x in (official | other))

    '''
  if pargs.upgrade:
    a, b, c = determine_upgradable(
      handle,
      check_aur=pargs.aur,
      aur_only=pargs.aur_only,
      aur_names=foreign_names
    )
    official |= a
    other |= b
    aur = c
  elif pargs.aur:
    aur = list(search_aur(foreign_names))
  else:
    aur = []
    '''

    # Ignore packages before parsing deps.
    official.ignore(ignored, ignoredgroups)
    other.ignore(ignored, ignoredgroups)


    # Resolve dependencies.
    if (official or other) and not pargs.nodeps:
        queue = deque(official | other)
        local_cache = handle.get_localdb().pkgcache
        syncdbs = handle.get_syncdbs()
        seen = set(queue)
        while queue:
            pkg = queue.popleft()
            for dep in pkg.depends:
                if pyalpm.find_satisfier(local_cache, dep) is None or pargs.alldeps:
                    for db in syncdbs:
                        prov = pyalpm.find_satisfier(db.pkgcache, dep)
                        if prov is not None:
                            if db.name in REPOSITORIES:
                                official.add(prov)
                            else:
                                other.add(prov)
                            if prov.name not in seen:
                                seen.add(prov.name)
                                queue.append(prov)
                            break
                    else:
                        missing_deps.append(dep)
        # Ignore packages after parsing deps.
        official.ignore(ignored, ignoredgroups)
        other.ignore(ignored, ignoredgroups)

    found |= set(official.pkgs) | set(other.pkgs)
    not_found = requested - found
    if pargs.needed:
        official = PkgSet(check_cache(conf, official))
        other = PkgSet(check_cache(conf, other))

    download_queue = DownloadQueue()

    if pargs.db:
        for db in handle.get_syncdbs():
            try:
                siglevel = conf[db.name]['SigLevel'].split()[0]
            except KeyError:
                siglevel = None
            download_sig = needs_sig(siglevel, pargs.sigs, 'Database')
            download_queue.add_db(db, download_sig)

    if official:
        if pargs.reflector:
            from Reflector import parse_args as parse_reflector_args, process_options, MirrorStatusError
            reflector_options = parse_reflector_args(pargs.reflector)
            try:
                mirrorstatus, mirrors = process_options(reflector_options)
                mirrors = list(m['url'] for m in mirrors)
            except MirrorStatusError as e:
                sys.stderr.write(str(e))
                mirrors = []
        else:
            mirrors = []
        for pkg in official:
            # This preserves the order of the user's mirrorlist files and prevents
            # duplicates from being added by Reflector.
            urls = list(os.path.join(url,pkg.filename) for url in pkg.db.servers)
            urls.extend(
                set(
                    os.path.join(MIRROR_URL_FORMAT.format(
                        m, pkg.db.name, pkg.arch), pkg.filename
                    ) for m in mirrors
                ) - set(urls)
            )

            try:
                siglevel = conf[pkg.db.name]['SigLevel'].split()[0]
            except KeyError:
                siglevel = None
            download_sig = needs_sig(siglevel, pargs.sigs, 'Package')
            download_queue.add_sync_pkg(pkg, urls, download_sig)

    for pkg in other:
        try:
            siglevel = conf[pkg.db.name]['SigLevel'].split()[0]
        except KeyError:
            siglevel = None
        download_sig = needs_sig(siglevel, pargs.sigs, 'Package')
        urls = set(os.path.join(url, pkg.filename) for url in pkg.db.servers)
        download_queue.add_sync_pkg(pkg, urls, download_sig)

  return download_queue, not_found, missing_deps

def get_checksum(path, typ):
    """ Returns checksum of a file """
    h = hashlib.new(typ)
    b = h.block_size
    try:
        with open(path, 'rb') as f:
          buf = f.read(b)
          while buf:
            h.update(buf)
            buf = f.read(b)
        return h.hexdigest()
    except IOError as err:
        if err.errno != errno.ENOENT:
            raise err

def check_cache(conf, pkgs):
    """ Checks package checksum in cache """
    for pkg in pkgs:
        for cache in conf.options['CacheDir']:
            fpath = os.path.join(cache, pkg.filename)
            for checksum in ('sha256', 'md5'):
                a = get_checksum(fpath, checksum)
                b = getattr(pkg, checksum + 'sum')
                if a is None or a != b:
                    yield pkg
                    break
            else:
                continue
            break

def needs_sig(siglevel, insistence, prefix):
    """ Determines if a signature should be downloaded.
        The siglevel is the pacman.conf SigLevel for the given repo.
        The insistence is an integer. Anything below 1 will return false,
            anything above 1 will return true, and 1 will check if the
            siglevel is required or optional.
        The prefix is either "Database" or "Package". """
    if insistence > 1:
        return True
    elif insistence == 1 and siglevel:
        for sl in ('Required', 'Optional'):
            if siglevel == sl or siglevel == prefix + sl:
                return True
    return False


''' Test case '''
if __name__ == '__main__':
    import gettext
    _ = gettext.gettext

    '''
    with open("/usr/share/cnchi/test/gnome-sudoku.meta4") as meta4:
        print(get_info(meta4.read()))
    '''
