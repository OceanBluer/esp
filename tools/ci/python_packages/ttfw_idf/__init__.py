# Copyright 2015-2017 Espressif Systems (Shanghai) PTE LTD
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import functools
import json
import logging
import os
import re
from copy import deepcopy

import junit_xml

from tiny_test_fw import TinyFW, Utility
from .DebugUtils import OCDBackend, GDBBackend, CustomProcess  # noqa: export DebugUtils for users
from .IDFApp import IDFApp, Example, LoadableElfTestApp, UT, TestApp, ComponentUTApp  # noqa: export all Apps for users
from .IDFDUT import IDFDUT, ESP32DUT, ESP32S2DUT, ESP32C3DUT, ESP8266DUT, ESP32QEMUDUT  # noqa: export DUTs for users
from .unity_test_parser import TestResults, TestFormat

# pass TARGET_DUT_CLS_DICT to Env.py to avoid circular dependency issue.
TARGET_DUT_CLS_DICT = {
    'ESP32': ESP32DUT,
    'ESP32S2': ESP32S2DUT,
    'ESP32C3': ESP32C3DUT,
}


def format_case_id(target, case_name):
    return "{}.{}".format(target, case_name)


try:
    string_type = basestring
except NameError:
    string_type = str


def upper_list_or_str(text):
    """
    Return the uppercase of list of string or string. Return itself for other
    data types
    :param text: list or string, other instance will be returned immediately
    :return: uppercase of list of string
    """
    if isinstance(text, string_type):
        return [text.upper()]
    elif isinstance(text, list):
        return [item.upper() for item in text]
    else:
        return text


def local_test_check(decorator_target):
    # Try to get the sdkconfig.json to read the IDF_TARGET value.
    # If not set, will set to ESP32.
    # For CI jobs, this is a fake procedure, the true target and dut will be
    # overwritten by the job config YAML file.
    idf_target = 'ESP32'  # default if sdkconfig not found or not readable
    if os.getenv('CI_JOB_ID'):  # Only auto-detect target when running locally
        return idf_target

    decorator_target = upper_list_or_str(decorator_target)
    expected_json_path = os.path.join('build', 'config', 'sdkconfig.json')
    if os.path.exists(expected_json_path):
        sdkconfig = json.load(open(expected_json_path))
        try:
            idf_target = sdkconfig['IDF_TARGET'].upper()
        except KeyError:
            logging.debug('IDF_TARGET not in {}. IDF_TARGET set to esp32'.format(os.path.abspath(expected_json_path)))
        else:
            logging.debug('IDF_TARGET: {}'.format(idf_target))
    else:
        logging.debug('{} not found. IDF_TARGET set to esp32'.format(os.path.abspath(expected_json_path)))

    if isinstance(decorator_target, list):
        if idf_target not in decorator_target:
            raise ValueError('IDF_TARGET set to {}, not in decorator target value'.format(idf_target))
    else:
        if idf_target != decorator_target:
            raise ValueError('IDF_TARGET set to {}, not equal to decorator target value'.format(idf_target))
    return idf_target


def get_dut_class(target, dut_class_dict, erase_nvs=None):
    if target not in dut_class_dict:
        raise Exception('target can only be {%s} (case insensitive)' % ', '.join(dut_class_dict.keys()))

    dut = dut_class_dict[target.upper()]
    if erase_nvs:
        dut.ERASE_NVS = 'erase_nvs'
    return dut


def ci_target_check(func):
    @functools.wraps(func)
    def wrapper(**kwargs):
        target = upper_list_or_str(kwargs.get('target', []))
        ci_target = upper_list_or_str(kwargs.get('ci_target', []))
        if not set(ci_target).issubset(set(target)):
            raise ValueError('ci_target must be a subset of target')

        return func(**kwargs)

    return wrapper


def test_func_generator(func, app, target, ci_target, module, execution_time, level, erase_nvs, **kwargs):
    target = upper_list_or_str(target)
    test_target = local_test_check(target)
    if 'additional_duts' in kwargs:
        dut_classes = deepcopy(TARGET_DUT_CLS_DICT)
        dut_classes.update(kwargs['additional_duts'])
    else:
        dut_classes = TARGET_DUT_CLS_DICT
    dut = get_dut_class(test_target, dut_classes, erase_nvs)
    original_method = TinyFW.test_method(
        app=app, dut=dut, target=target, ci_target=upper_list_or_str(ci_target),
        module=module, execution_time=execution_time, level=level, erase_nvs=erase_nvs,
        dut_dict=dut_classes, **kwargs
    )
    test_func = original_method(func)
    test_func.case_info["ID"] = format_case_id(target, test_func.case_info["name"])
    return test_func


@ci_target_check
def idf_example_test(app=Example, target="ESP32", ci_target=None, module="examples", execution_time=1,
                     level="example", erase_nvs=True, config_name=None, **kwargs):
    """
    decorator for testing idf examples (with default values for some keyword args).

    :param app: test application class
    :param target: target supported, string or list
    :param ci_target: target auto run in CI, if None than all target will be tested, None, string or list
    :param module: module, string
    :param execution_time: execution time in minutes, int
    :param level: test level, could be used to filter test cases, string
    :param erase_nvs: if need to erase_nvs in DUT.start_app()
    :param config_name: if specified, name of the app configuration
    :param kwargs: other keyword args
    :return: test method
    """
    def test(func):
        return test_func_generator(func, app, target, ci_target, module, execution_time, level, erase_nvs, **kwargs)
    return test


@ci_target_check
def idf_unit_test(app=UT, target="ESP32", ci_target=None, module="unit-test", execution_time=1,
                  level="unit", erase_nvs=True, **kwargs):
    """
    decorator for testing idf unit tests (with default values for some keyword args).

    :param app: test application class
    :param target: target supported, string or list
    :param ci_target: target auto run in CI, if None than all target will be tested, None, string or list
    :param module: module, string
    :param execution_time: execution time in minutes, int
    :param level: test level, could be used to filter test cases, string
    :param erase_nvs: if need to erase_nvs in DUT.start_app()
    :param kwargs: other keyword args
    :return: test method
    """
    def test(func):
        return test_func_generator(func, app, target, ci_target, module, execution_time, level, erase_nvs, **kwargs)
    return test


@ci_target_check
def idf_custom_test(app=TestApp, target="ESP32", ci_target=None, module="misc", execution_time=1,
                    level="integration", erase_nvs=True, config_name=None, **kwargs):
    """
    decorator for idf custom tests (with default values for some keyword args).

    :param app: test application class
    :param target: target supported, string or list
    :param ci_target: target auto run in CI, if None than all target will be tested, None, string or list
    :param module: module, string
    :param execution_time: execution time in minutes, int
    :param level: test level, could be used to filter test cases, string
    :param erase_nvs: if need to erase_nvs in DUT.start_app()
    :param config_name: if specified, name of the app configuration
    :param kwargs: other keyword args
    :return: test method
    """
    def test(func):
        return test_func_generator(func, app, target, ci_target, module, execution_time, level, erase_nvs, **kwargs)
    return test


@ci_target_check
def idf_component_unit_test(app=ComponentUTApp, target="ESP32", ci_target=None, module="misc", execution_time=1,
                            level="integration", erase_nvs=True, config_name=None, **kwargs):
    """
    decorator for idf custom tests (with default values for some keyword args).

    :param app: test application class
    :param target: target supported, string or list
    :param ci_target: target auto run in CI, if None than all target will be tested, None, string or list
    :param module: module, string
    :param execution_time: execution time in minutes, int
    :param level: test level, could be used to filter test cases, string
    :param erase_nvs: if need to erase_nvs in DUT.start_app()
    :param config_name: if specified, name of the app configuration
    :param kwargs: other keyword args
    :return: test method
    """

    def test(func):
        return test_func_generator(func, app, target, ci_target, module, execution_time, level, erase_nvs, **kwargs)

    return test


class ComponentUTResult:
    """
    Function Class, parse component unit test results
    """

    @staticmethod
    def parse_result(stdout):
        try:
            results = TestResults(stdout, TestFormat.UNITY_FIXTURE_VERBOSE)
        except (ValueError, TypeError) as e:
            raise ValueError('Error occurs when parsing the component unit test stdout to JUnit report: ' + str(e))

        group_name = results.tests()[0].group()
        with open(os.path.join(os.getenv('LOG_PATH', ''), '{}_XUNIT_RESULT.xml'.format(group_name)), 'w') as fw:
            junit_xml.to_xml_report_file(fw, [results.to_junit()])

        if results.num_failed():
            # raise exception if any case fails
            err_msg = 'Failed Cases:\n'
            for test_case in results.test_iter():
                if test_case.result() == 'FAIL':
                    err_msg += '\t{}: {}'.format(test_case.name(), test_case.message())
            raise AssertionError(err_msg)


def log_performance(item, value):
    """
    do print performance with pre-defined format to console

    :param item: performance item name
    :param value: performance value
    """
    performance_msg = "[Performance][{}]: {}".format(item, value)
    Utility.console_log(performance_msg, "orange")
    # update to junit test report
    current_junit_case = TinyFW.JunitReport.get_current_test_case()
    current_junit_case.stdout += performance_msg + "\r\n"


def check_performance(item, value, target):
    """
    check if idf performance meet pass standard

    :param item: performance item name
    :param value: performance item value
    :param target: target chip
    :raise: AssertionError: if check fails
    """

    def _find_perf_item(path):
        with open(path, 'r') as f:
            data = f.read()
        match = re.search(r'#define\s+IDF_PERFORMANCE_(MIN|MAX)_{}\s+([\d.]+)'.format(item.upper()), data)
        return match.group(1), float(match.group(2))

    def _check_perf(op, standard_value):
        if op == 'MAX':
            ret = value <= standard_value
        else:
            ret = value >= standard_value
        if not ret:
            raise AssertionError("[Performance] {} value is {}, doesn't meet pass standard {}"
                                 .format(item, value, standard_value))

    path_prefix = os.path.join(IDFApp.get_sdk_path(), 'components', 'idf_test', 'include')
    performance_files = (os.path.join(path_prefix, target, 'idf_performance_target.h'),
                         os.path.join(path_prefix, 'idf_performance.h'))

    for performance_file in performance_files:
        try:
            op, standard = _find_perf_item(performance_file)
        except (IOError, AttributeError):
            # performance file doesn't exist or match is not found in it
            continue

        _check_perf(op, standard)
        # if no exception was thrown then the performance is met and no need to continue
        break
    else:
        raise AssertionError("Failed to get performance standard for {}".format(item))


MINIMUM_FREE_HEAP_SIZE_RE = re.compile(r'Minimum free heap size: (\d+) bytes')


def print_heap_size(app_name, config_name, target, minimum_free_heap_size):
    """
    Do not change the print output in case you really need to.
    The result is parsed by ci-dashboard project
    """
    print('------ heap size info ------\n'
          '[app_name] {}\n'
          '[config_name] {}\n'
          '[target] {}\n'
          '[minimum_free_heap_size] {} Bytes\n'
          '------ heap size end ------'.format(app_name,
                                               '' if not config_name else config_name,
                                               target,
                                               minimum_free_heap_size))