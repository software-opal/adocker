import re
import typing as typ

import aiohttp

from .resource import Collection, ReloadableModel
from .image_history import ImageHistory
from ..api import APIClient
from ..errors import BuildError


class Image(ReloadableModel):
    """
    An image on the server.
    """
    def __repr__(self):
        return "<%s: '%s'>" % (self.__class__.__name__, "', '".join(self.tags))

    @property
    def labels(self) -> typ.Mapping[str, str]:
        """
        The labels of an image as dictionary.
        """
        result = self.attrs['Config'].get('Labels')
        return result or {}

    @property
    def short_id(self) -> str:
        """
        The ID of the image truncated to 10 characters, plus the ``sha256:``
        prefix.
        """
        if self.id.startswith('sha256:'):
            return self.id[:17]
        return self.id[:10]

    @property
    def tags(self) -> typ.List[str]:
        """
        The image's tags.
        """
        tags = self.attrs.get('RepoTags')
        if tags is None:
            tags = []
        return [tag for tag in tags if tag != '<none>:<none>']

    async def history(self) -> typ.Sequence[ImageHistory]:
        """
        List the history(parent layers) of an image.

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.
        """
        return tuple(
            self.collection.prepare_model(history_entry, model=ImageHistory)
            for history_entry in await self.client.api.history(self.id)
        )

    async def save(self) -> typ.Any:
        """
        Get a tarball of an image. Similar to the ``docker save`` command.

        Returns:
            (urllib3.response.HTTPResponse object): The response from the
            daemon.

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.

        Example:

            >>> image = cli.images.get("fedora:latest")
            >>> resp = image.save()
            >>> f = open('/tmp/fedora-latest.tar', 'w')
            >>> for chunk in resp.stream():
            >>>     f.write(chunk)
            >>> f.close()
        """
        return await self.client.api.get_image(self.id)

    async def tag(self, repository: str, tag: typ.Optional[str]=None, force: bool=False) -> bool:
        """
        Tag this image into a repository. Similar to the ``docker tag``
        command.

        Args:
            repository (str): The repository to set for the tag
            tag (str): The tag name
            force (bool): Force

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.

        Returns:
            (bool): ``True`` if successful
        """
        return await self.client.api.tag(self.id, repository, tag=tag, force=force)


class ImageCollection(Collection):
    model = Image

    def build(self, **kwargs):
        """
        Build an image and return it. Similar to the ``docker build``
        command. Either ``path`` or ``fileobj`` must be set.

        If you have a tar file for the Docker build context (including a
        Dockerfile) already, pass a readable file-like object to ``fileobj``
        and also pass ``custom_context=True``. If the stream is compressed
        also, set ``encoding`` to the correct value (e.g ``gzip``).

        If you want to get the raw output of the build, use the
        :py:meth:`~docker.api.build.BuildApiMixin.build` method in the
        low-level API.

        Args:
            path (str): Path to the directory containing the Dockerfile
            fileobj: A file object to use as the Dockerfile. (Or a file-like
                object)
            tag (str): A tag to add to the final image
            quiet (bool): Whether to return the status
            nocache (bool): Don't use the cache when set to ``True``
            rm (bool): Remove intermediate containers. The ``docker build``
                command now defaults to ``--rm=true``, but we have kept the old
                default of `False` to preserve backward compatibility
            timeout (int): HTTP timeout
            custom_context (bool): Optional if using ``fileobj``
            encoding (str): The encoding for a stream. Set to ``gzip`` for
                compressing
            pull (bool): Downloads any updates to the FROM image in Dockerfiles
            forcerm (bool): Always remove intermediate containers, even after
                unsuccessful builds
            dockerfile (str): path within the build context to the Dockerfile
            buildargs (dict): A dictionary of build arguments
            container_limits (dict): A dictionary of limits applied to each
                container created by the build process. Valid keys:

                - memory (int): set memory limit for build
                - memswap (int): Total memory (memory + swap), -1 to disable
                    swap
                - cpushares (int): CPU shares (relative weight)
                - cpusetcpus (str): CPUs in which to allow execution, e.g.,
                    ``"0-3"``, ``"0,1"``
            shmsize (int): Size of `/dev/shm` in bytes. The size must be
                greater than 0. If omitted the system uses 64MB
            labels (dict): A dictionary of labels to set on the image
            cache_from (list): A list of images used for build cache
                resolution
            target (str): Name of the build-stage to build in a multi-stage
                Dockerfile
            network_mode (str): networking mode for the run commands during
                build
            squash (bool): Squash the resulting images layers into a
                single layer.
            extra_hosts (dict): Extra hosts to add to /etc/hosts in building
                containers, as a mapping of hostname to IP address.

        Returns:
            (:py:class:`Image`): The built image.

        Raises:
            :py:class:`docker.errors.BuildError`
                If there is an error during the build.
            :py:class:`docker.errors.APIError`
                If the server returns any other error.
            ``TypeError``
                If neither ``path`` nor ``fileobj`` is specified.
        """
        resp = self.client.api.build(**kwargs)
        if isinstance(resp, six.string_types):
            return self.get(resp)
        last_event = None
        image_id = None
        for chunk in json_stream(resp):
            if 'error' in chunk:
                raise BuildError(chunk['error'])
            if 'stream' in chunk:
                match = re.search(
                    r'(^Successfully built |sha256:)([0-9a-f]+)$',
                    chunk['stream']
                )
                if match:
                    image_id = match.group(2)
            last_event = chunk
        if image_id:
            return self.get(image_id)
        raise BuildError(last_event or 'Unknown')

    def get(self, name):
        """
        Gets an image.

        Args:
            name (str): The name of the image.

        Returns:
            (:py:class:`Image`): The image.

        Raises:
            :py:class:`docker.errors.ImageNotFound`
                If the image does not exist.
            :py:class:`docker.errors.APIError`
                If the server returns an error.
        """
        return self.prepare_model(self.client.api.inspect_image(name))

    def list(self, name=None, all=False, filters=None):
        """
        List images on the server.

        Args:
            name (str): Only show images belonging to the repository ``name``
            all (bool): Show intermediate image layers. By default, these are
                filtered out.
            filters (dict): Filters to be processed on the image list.
                Available filters:
                - ``dangling`` (bool)
                - ``label`` (str): format either ``key`` or ``key=value``

        Returns:
            (list of :py:class:`Image`): The images.

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.
        """
        resp = self.client.api.images(name=name, all=all, filters=filters)
        return [self.get(r["Id"]) for r in resp]

    def load(self, data):
        """
        Load an image that was previously saved using
        :py:meth:`~docker.models.images.Image.save` (or ``docker save``).
        Similar to ``docker load``.

        Args:
            data (binary): Image data to be loaded.

        Returns:
            (generator): Progress output as JSON objects

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.
        """
        return self.client.api.load_image(data)

    def pull(self, name, tag=None, **kwargs):
        """
        Pull an image of the given name and return it. Similar to the
        ``docker pull`` command.

        If you want to get the raw pull output, use the
        :py:meth:`~docker.api.image.ImageApiMixin.pull` method in the
        low-level API.

        Args:
            name (str): The repository to pull
            tag (str): The tag to pull
            insecure_registry (bool): Use an insecure registry
            auth_config (dict): Override the credentials that
                :py:meth:`~docker.client.DockerClient.login` has set for
                this request. ``auth_config`` should contain the ``username``
                and ``password`` keys to be valid.

        Returns:
            (:py:class:`Image`): The image that has been pulled.

        Raises:
            :py:class:`docker.errors.APIError`
                If the server returns an error.

        Example:

            >>> image = client.images.pull('busybox')
        """
        self.client.api.pull(name, tag=tag, **kwargs)
        return self.get('{0}:{1}'.format(name, tag) if tag else name)

    def push(self, repository, tag=None, **kwargs):
        return self.client.api.push(repository, tag=tag, **kwargs)
    push.__doc__ = APIClient.push.__doc__

    def remove(self, *args, **kwargs):
        self.client.api.remove_image(*args, **kwargs)
    remove.__doc__ = APIClient.remove_image.__doc__

    def search(self, *args, **kwargs):
        return self.client.api.search(*args, **kwargs)
    search.__doc__ = APIClient.search.__doc__

    def prune(self, filters=None):
        return self.client.api.prune_images(filters=filters)
    prune.__doc__ = APIClient.prune_images.__doc__
