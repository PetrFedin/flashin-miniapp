import React from "react";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorId: "" };
  }

  static getDerivedStateFromError() {
    return {
      hasError: true,
      errorId: `FE-${Date.now().toString(36).toUpperCase()}`,
    };
  }

  componentDidCatch(error, errorInfo) {
    console.error("FLASHIN frontend crash", {
      error,
      errorInfo,
      errorId: this.state.errorId,
      location: window.location.href,
      userAgent: navigator.userAgent,
    });
  }

  handleRetry = () => {
    this.setState({ hasError: false, errorId: "" });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <main className="fatal-screen" role="alert">
        <div className="fatal-card">
          <div className="fatal-brand">FLASHIN</div>
          <h1>Не удалось открыть экран</h1>
          <p>
            Приложение столкнулось с ошибкой интерфейса. Корзина и данные аккаунта
            не удалены.
          </p>
          <div className="fatal-actions">
            <button className="primary" type="button" onClick={this.handleRetry}>
              Попробовать ещё раз
            </button>
            <button className="secondary" type="button" onClick={this.handleReload}>
              Перезапустить приложение
            </button>
          </div>
          <small>Код ошибки: {this.state.errorId}</small>
        </div>
      </main>
    );
  }
}

export default ErrorBoundary;
