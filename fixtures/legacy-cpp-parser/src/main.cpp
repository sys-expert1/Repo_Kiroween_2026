#include <iostream>

#include "parser.h"

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: parse_cli <key=value>..." << std::endl;
        return 2;
    }
    for (int i = 1; i < argc; ++i) {
        for (const auto& entry : parse_config(argv[i])) {
            std::cout << entry.first << " -> " << entry.second << std::endl;
        }
    }
    return 0;
}
