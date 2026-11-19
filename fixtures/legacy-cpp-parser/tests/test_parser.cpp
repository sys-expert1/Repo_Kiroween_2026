#include <cstring>
#include <iostream>

#include "parser.h"

static int check_parse() {
    auto config = parse_config("name=widget\nsize=3\n");
    return config["name"] == "widget" && config["size"] == "3" ? 0 : 1;
}

static int check_trim() {
    // Baseline failure on purpose: the legacy parser keeps the spaces around the value.
    auto config = parse_config("name = widget\n");
    return config["name "] == " widget" && config["name"] == "widget" ? 0 : 1;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: test_parser <parse|trim>" << std::endl;
        return 2;
    }
    if (std::strcmp(argv[1], "parse") == 0) {
        return check_parse();
    }
    if (std::strcmp(argv[1], "trim") == 0) {
        return check_trim();
    }
    return 2;
}
