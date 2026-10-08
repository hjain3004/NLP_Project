import Foundation
import LocalAuthentication

let context = LAContext()
let semaphore = DispatchSemaphore(value: 0)
let reason = CommandLine.arguments.dropFirst().joined(separator: " ")
var authenticated = false
var cancelled = false

context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, error in
    authenticated = success
    if let error = error as NSError?,
       error.domain == LAError.errorDomain,
       error.code == LAError.userCancel.rawValue {
        cancelled = true
    }
    semaphore.signal()
}

_ = semaphore.wait(timeout: .distantFuture)
if authenticated {
    print("authenticated")
    exit(0)
}
if cancelled {
    exit(2)
}
exit(3)
