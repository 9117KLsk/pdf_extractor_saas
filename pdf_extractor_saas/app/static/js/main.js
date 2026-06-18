// Funciones auxiliares para la interfaz
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.role = 'alert';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    document.querySelector('.container').prepend(alertDiv);
}

// Ejemplo de uso: si hay errores en el formulario
document.addEventListener('DOMContentLoaded', function() {
    console.log('PDF Extractor SaaS listo');
});