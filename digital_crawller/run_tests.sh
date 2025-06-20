#!/bin/bash
# run_tests.sh - Script to run tests for the digital crawler

# Set default values
GATEWAY_URL=${GATEWAY_API_URL:-"http://gateway:5000"}
TEST_URL="https://www.ndtv.com/india-news/pm-modi-to-visit-odisha-today-to-launch-projects-worth-rs-2-000-crore-5195839"

# Print header
echo "====================================="
echo "Digital Crawler Test Suite"
echo "====================================="
echo "Gateway URL: $GATEWAY_URL"
echo

# Function to run a test
run_test() {
    local test_name=$1
    local test_command=$2
    
    echo "Running test: $test_name"
    echo "Command: $test_command"
    echo "-------------------------------------"
    eval $test_command
    local result=$?
    
    if [ $result -eq 0 ]; then
        echo "✅ Test passed: $test_name"
    else
        echo "❌ Test failed: $test_name"
    fi
    
    echo "-------------------------------------"
    echo
    
    return $result
}

# Check if a specific test was requested
if [ "$1" != "" ]; then
    case $1 in
        "gateway")
            run_test "Gateway Connection" "python test_gateway_connection.py"
            exit $?
            ;;
        "direct")
            run_test "Direct Gateway Sender" "python test_direct_gateway.py --test-direct"
            exit $?
            ;;
        "crawler")
            run_test "Crawler with URL" "python test_direct_gateway.py --test-crawler --url $TEST_URL"
            exit $?
            ;;
        "newsplease")
            run_test "Crawler with NewsPlease" "python test_direct_gateway.py --test-newsplease --url $TEST_URL"
            exit $?
            ;;
        "retry")
            run_test "Retry Pending Uploads" "python test_direct_gateway.py --test-retry"
            exit $?
            ;;
        "full")
            run_test "Full Flow Test" "python test_full_flow.py"
            exit $?
            ;;
        "all")
            # Run all tests
            ;;
        *)
            echo "Unknown test: $1"
            echo "Available tests: gateway, direct, crawler, newsplease, retry, full, all"
            exit 1
            ;;
    esac
fi

# Run all tests
echo "Running all tests..."
echo

# Test 1: Gateway Connection
run_test "Gateway Connection" "python test_gateway_connection.py"
gateway_result=$?

# Test 2: Direct Gateway Sender
run_test "Direct Gateway Sender" "python test_direct_gateway.py --test-direct"
direct_result=$?

# Test 3: Crawler with URL
run_test "Crawler with URL" "python test_direct_gateway.py --test-crawler --url $TEST_URL"
crawler_result=$?

# Test 4: Crawler with NewsPlease
run_test "Crawler with NewsPlease" "python test_direct_gateway.py --test-newsplease --url $TEST_URL"
newsplease_result=$?

# Test 5: Retry Pending Uploads
run_test "Retry Pending Uploads" "python test_direct_gateway.py --test-retry"
retry_result=$?

# Test 6: Full Flow Test
run_test "Full Flow Test" "python test_full_flow.py"
full_result=$?

# Print summary
echo "====================================="
echo "Test Summary"
echo "====================================="
echo "Gateway Connection: $([ $gateway_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "Direct Gateway Sender: $([ $direct_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "Crawler with URL: $([ $crawler_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "Crawler with NewsPlease: $([ $newsplease_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "Retry Pending Uploads: $([ $retry_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "Full Flow Test: $([ $full_result -eq 0 ] && echo '✅ Passed' || echo '❌ Failed')"
echo "====================================="

# Calculate overall result
overall_result=$(( $gateway_result + $direct_result + $crawler_result + $newsplease_result + $retry_result + $full_result ))

if [ $overall_result -eq 0 ]; then
    echo "✅ All tests passed!"
    exit 0
else
    echo "❌ Some tests failed. Please check the logs."
    exit 1
fi