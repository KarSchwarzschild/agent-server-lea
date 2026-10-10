package temporalrun

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/testsuite"
)

func TestAgentRunWorkflowSuccess(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	env.RegisterActivityWithOptions(func(context.Context, Input) error { return nil }, activity.RegisterOptions{Name: ExecuteActivityName})
	env.RegisterActivityWithOptions(func(context.Context, FinalizeInput) error {
		t.Fatal("finalizer should not run after successful execution")
		return nil
	}, activity.RegisterOptions{Name: FinalizeActivityName})

	env.ExecuteWorkflow(AgentRunWorkflow, Input{RunID: "run-1"})
	require.NoError(t, env.GetWorkflowError())
}

func TestAgentRunWorkflowFinalizesFailure(t *testing.T) {
	var suite testsuite.WorkflowTestSuite
	env := suite.NewTestWorkflowEnvironment()
	attempts := 0
	env.RegisterActivityWithOptions(func(context.Context, Input) error {
		attempts++
		return errors.New("worker disappeared")
	}, activity.RegisterOptions{Name: ExecuteActivityName})
	var finalized FinalizeInput
	env.RegisterActivityWithOptions(func(_ context.Context, input FinalizeInput) error {
		finalized = input
		return nil
	}, activity.RegisterOptions{Name: FinalizeActivityName})

	env.ExecuteWorkflow(AgentRunWorkflow, Input{RunID: "run-2"})
	require.Error(t, env.GetWorkflowError())
	require.Equal(t, "run-2", finalized.RunID)
	require.False(t, finalized.Cancelled)
	require.Contains(t, finalized.Error, "worker disappeared")
	require.Equal(t, 3, attempts)
}
