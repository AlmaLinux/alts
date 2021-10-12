import hashlib


def get_hasher(checksum_type):
    """
    Returns a corresponding hashlib hashing function for the specified checksum
    type.

    Parameters
    ----------
    checksum_type : str
        Checksum type (e.g. sha1, sha256).

    Returns
    -------
    _hashlib.HASH
        Hashlib hashing function.
    """
    return hashlib.new('sha1' if checksum_type == 'sha' else checksum_type)


def hash_file(file_path, hasher=None, hash_type=None, buff_size=1048576):
    """
    Returns checksum (hexadecimal digest) of the file.

    Parameters
    ----------
    file_path : str or file-like
        File to hash. It could be either a path or a file descriptor.
    hasher : _hashlib.HASH
        Any hash algorithm from hashlib.
    hash_type : str
        Hash type (e.g. sha1, sha256).
    buff_size : int
        Number of bytes to read at once.

    Returns
    -------
    str
        Checksum (hexadecimal digest) of the file.
    """
    if hasher is None:
        hasher = get_hasher(hash_type)

    def feed_hasher(_fd):
        buff = _fd.read(buff_size)
        while len(buff):
            if not isinstance(buff, bytes):
                buff = buff.encode('utf')
            hasher.update(buff)
            buff = _fd.read(buff_size)
    if isinstance(file_path, str):
        with open(file_path, "rb") as fd:
            feed_hasher(fd)
    else:
        file_path.seek(0)
        feed_hasher(file_path)
    return hasher.hexdigest()
