#!/bin/bash
while :; do :; done & kill -STOP $! && wait $!
