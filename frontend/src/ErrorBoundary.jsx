import React from "react";
import { captureUiError } from "./observability.js";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    captureUiError(error, "storefront-root");
  }

  render() {
    if (this.state.hasError) {
      return <h2>Что-то пошло не так. Обновите приложение и повторите попытку.</h2>;
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
