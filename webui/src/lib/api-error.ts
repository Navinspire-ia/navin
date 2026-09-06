/**
 * Lives apart from the API client so that modules the client itself depends on
 * can recognise its errors without importing it back.
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}
