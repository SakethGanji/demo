import { Component, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from './ui/button';
import { EmptyState } from './ui/empty-state';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <EmptyState
          className="h-full w-full"
          icon={<AlertTriangle className="text-destructive" />}
          title="Something went wrong"
          actions={
            <>
              <Button variant="outline" onClick={this.handleReset}>
                <RefreshCw />
                Try again
              </Button>
              <Button onClick={() => window.location.reload()}>Refresh page</Button>
            </>
          }
        >
          An unexpected error occurred. Try refreshing the page or click the button below to recover.
          {this.state.error && (
            <pre className="well mt-3 max-h-32 w-full overflow-auto p-3 text-left font-mono text-[11.5px] text-foreground">{this.state.error.message}</pre>
          )}
        </EmptyState>
      );
    }

    return this.props.children;
  }
}
