# repo.py
# DNF Repository objects.
#
# Copyright (C) 2013  Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# the GNU General Public License v.2, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY expressed or implied, including the implied warranties of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.  You should have received a copy of the
# GNU General Public License along with this program; if not, write to the
# Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.  Any Red Hat trademarks that are incorporated in the
# source code or documentation are not subject to the GNU General Public
# License and may only be used or replicated with the express permission of
# Red Hat, Inc.
#

import dnf.util
import dnf.yum.Errors
import dnf.yum.config
import librepo
import os.path
import time

class _Result(object):
    def __init__(self, res):
        self.repo_dct = res.getinfo(librepo.LRR_YUM_REPO)
        self.repomd_dct = res.getinfo(librepo.LRR_YUM_REPOMD)

    @property
    def age(self):
        return self.file_age("primary")

    def file_age(self, what):
        f_ts = dnf.util.file_timestamp(self.repo_dct[what])
        return time.time() - f_ts

    @property
    def filelists_fn(self):
        return self.repo_dct.get('filelists')

    @property
    def presto_fn(self):
        return self.repo_dct.get('prestodelta')

    @property
    def primary_fn(self):
        return self.repo_dct.get('primary')

    @property
    def repomd_fn(self):
        return self.repo_dct.get('repomd')

class _Handle(librepo.Handle):
    def __init__(self, gpgcheck):
        super(_Handle, self).__init__()
        self.setopt(librepo.LRO_REPOTYPE, librepo.LR_YUMREPO)
        self.setopt(librepo.LRO_YUMDLIST, ["primary", "filelists", "prestodelta"])
        self.setopt(librepo.LRO_GPGCHECK, gpgcheck)

    @classmethod
    def new_local(cls, gpgcheck, cachedir):
        h = cls(gpgcheck)
        h.destdir = cachedir
        h.setopt(librepo.LRO_URL, cachedir)
        h.local = True
        return h

    @classmethod
    def new_remote(cls, gpgcheck, destdir, mirror_setup, progress_cb):
        h = cls(gpgcheck)
        h.destdir = destdir
        h.setopt(mirror_setup[0], mirror_setup[1])
        if progress_cb is not None:
            h.setopt(librepo.LRO_PROGRESSCB, progress_cb)
        return h

    @property
    def destdir(self):
        return self.getinfo(librepo.LRI_DESTDIR)

    @destdir.setter
    def destdir(self, val):
        self.setopt(librepo.LRO_DESTDIR, val)

    @property
    def local(self):
        return self.getinfo(librepo.LRI_LOCAL)

    @local.setter
    def local(self, val):
        self.setopt(librepo.LRO_LOCAL, val)

SYNC_TRY_CACHE = 1
SYNC_NO_CACHE = 2

class Repo(dnf.yum.config.RepoConf):
    DEFAULT_SYNC = SYNC_TRY_CACHE

    def __init__(self, id_):
        super(Repo, self).__init__()
        self._progress = None
        self.id = id_
        self.basecachedir = None
        self.fallback_basecachedir = None
        self.base_persistdir = ""
        self.res = None
        self.sync_strategy = self.DEFAULT_SYNC
        self.yumvar = {} # empty dict of yumvariables for $string replacement

    def _handle_load(self, handle):
        r = librepo.Result()
        if self._handle_uses_callback(handle):
            self._progress.begin(self.name)
        handle.perform(r)
        if self._handle_uses_callback(handle):
            self._progress.end()
        return _Result(r)

    def _handle_new_local(self, destdir):
        return _Handle.new_local(self.repo_gpgcheck, destdir)

    def _handle_new_remote(self, destdir):
        cb = None
        if self._progress is not None:
            cb = self._progress.librepo_cb
        return _Handle.new_remote(self.repo_gpgcheck, destdir,
                                  self._mirror_setup_args(), cb)

    def _mirror_setup_args(self):
        if self.metalink:
            return librepo.LRO_MIRRORLIST, self.metalink
        elif self.mirrorlist:
            return librepo.LRO_MIRRORLIST, self.mirrorlist
        elif self.baseurl:
            return librepo.LRO_URL, self.baseurl[0]
        else:
            msg = 'Cannot find a valid baseurl for repo: %s' % self.id
            raise dnf.yum.Errors.RepoError, msg

    def _handle_uses_callback(self, handle):
        return self._progress is not None and not handle.local

    def _try_cache(self):
        if self.sync_strategy == SYNC_NO_CACHE:
            self.sync_strategy = self.DEFAULT_SYNC
            return False
        if self.res:
            return True
        handle = self._handle_new_local(self.cachedir)
        try:
            self.res = self._handle_load(handle)
        except librepo.LibrepoException as e:
            return False
        return self.res.file_age("primary") < self.metadata_expire

    @property
    def cachedir(self):
        return os.path.join(self.basecachedir, self.id)

    def dump(self):
        return ''

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    def error_message(self, exception):
        msg = "Problem with repo '%s': %s" % (self.id, str(exception))
        print msg

    def expire_cache(self):
        self.res = None
        self.sync_strategy = SYNC_NO_CACHE

    @property
    def filelists_fn(self):
        return self.res.filelists_fn

    def get_package(self, pkg, text=None):
        dnf.util.ensure_dir(self.pkgdir)
        handle = self._handle_new_remote(self.pkgdir)
        if self._handle_uses_callback(handle):
            text = text if text is not None else pkg.location
            self._progress.begin(text)
        handle.download(pkg.location)
        if self._handle_uses_callback(handle):
            self._progress.end()
        return pkg.localPkg()

    def metadata_expire_in(self):
        """Get the number of seconds after which the metadata will expire.

        Returns a tuple, boolean whether the information can be obtained and the
        number of seconds. Negative number means the metadata has expired
        already.

        """
        self._try_cache()
        if self.res:
            return (True, self.metadata_expire - self.res.age)
        return (False, 0)

    @property
    def presto_fn(self):
        return self.res.presto_fn

    @property
    def pkgdir(self):
        return os.path.join(self.cachedir, 'packages')

    @property
    def primary_fn(self):
        return self.res.primary_fn

    def replace_cache(self, from_dir):
        dnf.util.rm_rf(self.cachedir)
        os.rename(from_dir, self.cachedir)

    @property
    def repomd_fn(self):
        return self.res.repomd_fn

    def load(self):
        """Load the metadata for this repo.

        Depending on the configuration and the age and consistence of data
        available on the disk cache, either loads the metadata from the cache or
        downloads them from the mirror, baseurl or metalink.

        This method will not try to refresh the loaded data if called twice, IOW
        the loading is by default lazy.

        Returns True if this call to load() caused a fresh metadata download.

        """
        if self._try_cache():
            return False
        try:
            handle = self._handle_new_remote(dnf.util.tmpdir())
            self._handle_load(handle)
            self.replace_cache(handle.destdir)

            # get everything from the cache now:
            handle = self._handle_new_local(self.cachedir)
            self.res = self._handle_load(handle)
        except librepo.LibrepoException as e:
            self.res = None
            msg = str(e)
            self.error_message(msg)
            raise dnf.yum.Errors.RepoError(msg)
        return True

    def set_failure_callback(self, cb):
        pass

    def set_interrupt_callback(self, cb):
        pass

    def set_progress_bar(self, progress):
        self._progress = progress