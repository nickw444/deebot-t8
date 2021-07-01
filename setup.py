import os

from setuptools import setup


def get_version():
    version_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'VERSION')
    v = open(version_path).read()
    if type(v) == str:
        return v.strip()
    return v.decode('UTF-8').strip()


readme_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)),
    'README.md',
)
long_description = open(readme_path).read()

try:
    version = get_version()
except Exception:
    version = '0.0.0-dev'

setup(
    name='deebot-t8',
    version=version,
    packages=['deebot_t8', 'deebot_t8.cli'],
    author="Nick Whyte",
    author_email='nick@nickwhyte.com',
    description="Yet another Ecovacs Deebot API client",
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/nickw444/deebot-t8',
    zip_safe=False,
    classifiers=[
        'Intended Audience :: Developers',
        'Programming Language :: Python',
    ],
    install_requires=[
        'requests==2.25.1',
        'dataclasses;python_version<"3.7"',
        'paho-mqtt==1.5.1'
    ],
    extras_require={
        'cli': ['click==8.0.1', 'terminaltables==3.1.0']
    },
    entry_points={
        'console_scripts': ['deebot-t8=deebot_t8.cli.__main__:cli'],
    },
)
