# Copyright 2019 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""
validators for text ops
"""

from functools import wraps
import mindspore.common.dtype as mstype

import mindspore._c_dataengine as cde
from mindspore._c_expression import typing

from ..core.validator_helpers import parse_user_args, type_check, type_check_list, check_uint32, check_positive, \
    INT32_MAX, check_value


def check_unique_list_of_words(words, arg_name):
    """Check that words is a list and each element is a str without any duplication"""

    type_check(words, (list,), arg_name)
    words_set = set()
    for word in words:
        type_check(word, (str,), arg_name)
        if word in words_set:
            raise ValueError(arg_name + " contains duplicate word: " + word + ".")
        words_set.add(word)
    return words_set


def check_lookup(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [vocab, unknown], _ = parse_user_args(method, *args, **kwargs)

        if unknown is not None:
            type_check(unknown, (int,), "unknown")
            check_positive(unknown)
        type_check(vocab, (cde.Vocab,), "vocab is not an instance of cde.Vocab.")

        return method(self, *args, **kwargs)

    return new_method


def check_from_file(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [file_path, delimiter, vocab_size, special_tokens, special_first], _ = parse_user_args(method, *args,
                                                                                               **kwargs)
        check_unique_list_of_words(special_tokens, "special_tokens")
        type_check_list([file_path, delimiter], (str,), ["file_path", "delimiter"])
        if vocab_size is not None:
            check_value(vocab_size, (-1, INT32_MAX), "vocab_size")
        type_check(special_first, (bool,), special_first)

        return method(self, *args, **kwargs)

    return new_method


def check_from_list(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [word_list, special_tokens, special_first], _ = parse_user_args(method, *args, **kwargs)

        word_set = check_unique_list_of_words(word_list, "word_list")
        if special_tokens is not None:
            token_set = check_unique_list_of_words(special_tokens, "special_tokens")

            intersect = word_set.intersection(token_set)

            if intersect != set():
                raise ValueError("special_tokens and word_list contain duplicate word :" + str(intersect) + ".")

        type_check(special_first, (bool,), "special_first")

        return method(self, *args, **kwargs)

    return new_method


def check_from_dict(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [word_dict], _ = parse_user_args(method, *args, **kwargs)

        type_check(word_dict, (dict,), "word_dict")

        for word, word_id in word_dict.items():
            type_check(word, (str,), "word")
            type_check(word_id, (int,), "word_id")
            check_value(word_id, (-1, INT32_MAX), "word_id")
        return method(self, *args, **kwargs)

    return new_method


def check_jieba_init(method):
    """Wrapper method to check the parameters of jieba add word."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        parse_user_args(method, *args, **kwargs)
        return method(self, *args, **kwargs)

    return new_method


def check_jieba_add_word(method):
    """Wrapper method to check the parameters of jieba add word."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [word, freq], _ = parse_user_args(method, *args, **kwargs)
        if word is None:
            raise ValueError("word is not provided.")
        if freq is not None:
            check_uint32(freq)
        return method(self, *args, **kwargs)

    return new_method


def check_jieba_add_dict(method):
    """Wrapper method to check the parameters of add dict."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        parse_user_args(method, *args, **kwargs)
        return method(self, *args, **kwargs)

    return new_method


def check_from_dataset(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):

        [_, columns, freq_range, top_k, special_tokens, special_first], _ = parse_user_args(method, *args,
                                                                                            **kwargs)
        if columns is not None:
            if not isinstance(columns, list):
                columns = [columns]
                col_names = ["col_{0}".format(i) for i in range(len(columns))]
                type_check_list(columns, (str,), col_names)

        if freq_range is not None:
            type_check(freq_range, (tuple,), "freq_range")

            if len(freq_range) != 2:
                raise ValueError("freq_range needs to be a tuple of 2 integers or an int and a None.")

            for num in freq_range:
                if num is not None and (not isinstance(num, int)):
                    raise ValueError(
                        "freq_range needs to be either None or a tuple of 2 integers or an int and a None.")

            if isinstance(freq_range[0], int) and isinstance(freq_range[1], int):
                if freq_range[0] > freq_range[1] or freq_range[0] < 0:
                    raise ValueError("frequency range [a,b] should be 0 <= a <= b (a,b are inclusive).")

        type_check(top_k, (int, type(None)), "top_k")

        if isinstance(top_k, int):
            check_value(top_k, (0, INT32_MAX), "top_k")
        type_check(special_first, (bool,), "special_first")

        if special_tokens is not None:
            check_unique_list_of_words(special_tokens, "special_tokens")

        return method(self, *args, **kwargs)

    return new_method


def check_ngram(method):
    """A wrapper that wrap a parameter checker to the original function."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [n, left_pad, right_pad, separator], _ = parse_user_args(method, *args, **kwargs)

        if isinstance(n, int):
            n = [n]

        if not (isinstance(n, list) and n != []):
            raise ValueError("n needs to be a non-empty list of positive integers.")

        for i, gram in enumerate(n):
            type_check(gram, (int,), "gram[{0}]".format(i))
            check_value(gram, (0, INT32_MAX), "gram_{}".format(i))

        if not (isinstance(left_pad, tuple) and len(left_pad) == 2 and isinstance(left_pad[0], str) and isinstance(
                left_pad[1], int)):
            raise ValueError("left_pad needs to be a tuple of (str, int) str is pad token and int is pad_width.")

        if not (isinstance(right_pad, tuple) and len(right_pad) == 2 and isinstance(right_pad[0], str) and isinstance(
                right_pad[1], int)):
            raise ValueError("right_pad needs to be a tuple of (str, int) str is pad token and int is pad_width.")

        if not (left_pad[1] >= 0 and right_pad[1] >= 0):
            raise ValueError("padding width need to be positive numbers.")

        type_check(separator, (str,), "separator")

        kwargs["n"] = n
        kwargs["left_pad"] = left_pad
        kwargs["right_pad"] = right_pad
        kwargs["separator"] = separator

        return method(self, **kwargs)

    return new_method


def check_pair_truncate(method):
    """Wrapper method to check the parameters of number of pair truncate."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        parse_user_args(method, *args, **kwargs)
        return method(self, *args, **kwargs)

    return new_method


def check_to_number(method):
    """A wrapper that wraps a parameter check to the original function (ToNumber)."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [data_type], _ = parse_user_args(method, *args, **kwargs)
        type_check(data_type, (typing.Type,), "data_type")

        if data_type not in mstype.number_type:
            raise TypeError("data_type is not numeric data type.")

        return method(self, *args, **kwargs)

    return new_method


def check_python_tokenizer(method):
    """A wrapper that wraps a parameter check to the original function (PythonTokenizer)."""

    @wraps(method)
    def new_method(self, *args, **kwargs):
        [tokenizer], _ = parse_user_args(method, *args, **kwargs)

        if not callable(tokenizer):
            raise TypeError("tokenizer is not a callable python function")

        return method(self, *args, **kwargs)

    return new_method
